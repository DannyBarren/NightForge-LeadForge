"""Research graph — demand-side discovery on LangGraph.

``preflight → discover → parallel research → pitch → export → emit signals``,
with a governor gate node in front of each phase that spends money, and a halt
node that routes to export so partial work still lands on disk.

**This graph looks for homeowners, not shops.** The prompts come from
``nightforge.discovery.prompts`` and the demo seeds from
``nightforge.discovery.sample_demand``: property owners who publicly asked for
HVAC, plumbing, electrical, or roofing work. The pitch stage drafts the reply a
local shop would send that homeowner. ``leadforge/tasks.py`` and
``leadforge/sample_data.py`` still hold the shop-side prompts and seeds and are
untouched — the CrewAI prototype keeps finding shops exactly as it did.

What is still shared rather than forked: search and page fetch from
``leadforge.tools``, the Pydantic contracts from ``leadforge.models``, and the
CSV shape from ``leadforge.export.EXPORT_COLUMNS``. A homeowner is mapped onto
those business-shaped columns — ``Company`` carries a household label,
``Owner`` the posting name, ``Pains`` the request — so the export contract does
not fork.

After export, the discovered demand is emitted as ``SignalEvent`` objects into
``nightforge.scoring``, which is why ``research --sample`` and ``score --sample``
show the same three households.

Three things differ from the prototype, each deliberate:

- **Paid search is governed.** Every search call passes ``can_afford`` and is
  booked on the ledger. In the prototype, Tavily and Brave spend never reached
  the cost tracker.
- **No agent loop.** CrewAI let an agent iterate tool calls on its own. Each
  node here does a bounded pass — gate, search, gate, one LLM call — which is
  what makes the per-call gating exhaustive rather than best-effort.
- **Sample mode tolerates a failed preflight.** The prototype raises when a
  provider key is missing. Here, a sample run without keys skips the live
  attempt and goes straight to the seeds and fallbacks, because a demo that
  dies on a missing key is exactly what sample mode exists to prevent. A
  production run still halts.

Parallel research runs in a thread pool inside one node rather than as a graph
fan-out. That keeps the prototype's concurrency semantics exactly — the same
submit-time budget projection, the same worker clamp — and the governor is
thread-safe for precisely this.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from leadforge.config_loader import (
    AppConfig,
    get_config,
    resolve_industries,
    resolve_location,
)
from leadforge.export import export_pitches, output_dir
from leadforge.json_utils import extract_json_array, extract_json_object
from leadforge.models import (
    ConfidenceLevel,
    DiscoveredLead,
    LeadResearch,
    PitchOutput,
    RunManifest,
)
from leadforge.preflight import run_preflight
from leadforge.run_context import RunOptions
from leadforge.tools import build_research_tools, build_search_tools
from nightforge.discovery.prompts import (
    demand_discovery_prompt,
    demand_pitch_prompt,
    demand_research_prompt,
)
from nightforge.discovery.sample_demand import (
    SAMPLE_SERVICE_AREA_ZIPS,
    SAMPLE_ZONE_MAP,
    sample_demand_events,
    sample_demand_leads,
)
from nightforge.discovery.weather import weather_trigger_for
from nightforge.governor import (
    EST_DISCOVERY_IN,
    EST_DISCOVERY_OUT,
    EST_PITCH_IN,
    EST_PITCH_OUT,
    EST_RESEARCH_IN,
    EST_RESEARCH_OUT,
    Governor,
    GovernorHalt,
    LoopLimits,
)
from nightforge.scoring.pipeline import ingest as ingest_signals
from nightforge.scoring.schema import SignalEvent

logger = logging.getLogger(__name__)

# A search request is priced per call, not per token. Until a real per-request
# rate is configured it is gated and booked at the governor's estimate floor —
# approximate, but never free, which is the property that matters for a cap.
EST_SEARCH_IN, EST_SEARCH_OUT = 200, 200

# Signals emitted by a discovery pass. Weak on purpose: it is not in
# ``features.STRONG_SOURCES``, because our own crawl finding a post is weaker
# evidence than the homeowner posting it in their own words.
DISCOVERY_SIGNAL_SOURCE = "nightforge_discovery"
WEATHER_SIGNAL_SOURCE = "weather_alert"

# One signal per corroborating public source, capped so a lead with a long
# citation list cannot manufacture a hot band on its own.
MAX_SIGNALS_PER_LEAD = 3

_ZIP_PATTERN = re.compile(r"\b(\d{5})(?:-\d{4})?\b")

# ``Household — J. Doe (85001)`` is the label format the discovery prompt asks
# for, so the posting name can be read back out of it for the Owner column
# without adding a field to the shared DiscoveredLead contract.
_HOUSEHOLD_LABEL = re.compile(r"^household\s*[—–-]\s*(.+?)\s*(?:\(|$)", re.IGNORECASE)


class ResearchState(TypedDict, total=False):
    """State threaded through the research graph.

    ``config`` and ``governor`` ride in state rather than being read from a
    process global, so concurrent runs cannot see each other's budgets.
    """

    run_id: str
    sample_mode: bool
    config: Any
    governor: Any
    manifest: RunManifest
    preflight_ok: bool
    live: bool
    leads: list[DiscoveredLead]
    research: list[LeadResearch]
    pitches: list[PitchOutput]
    stopped_early: bool
    stop_reason: str | None
    export_paths: dict[str, str]
    summary: dict[str, Any]
    scored_leads: list[Any]


def default_loop_limits(max_leads: int) -> LoopLimits:
    """Bound a run relative to its own size.

    One discovery call plus a research and a pitch call per lead, with slack for
    retries. A fixed ceiling would either strangle a 30-lead run or wave through
    a wedged 3-lead one.
    """
    return LoopLimits(
        max_iterations=4 + 2 * max_leads,
        max_tool_calls=4 + 3 * max_leads,
    )


# ---- LLM and tool calls, each gated ------------------------------------


def _build_chat_model(config: AppConfig) -> Any:
    """Mirror ``leadforge.llm_factory`` for LangChain chat models."""
    provider = config.llm.provider.lower()
    model = config.llm.model
    max_tokens = min(
        config.llm.max_tokens, config.guardrails.per_agent_max_output_tokens
    )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model, temperature=config.llm.temperature, max_tokens=max_tokens
        )
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model, temperature=config.llm.temperature, max_tokens=max_tokens
        )
    if provider == "xai":
        import os

        from langchain_openai import ChatOpenAI

        api_key = os.getenv("XAI_API_KEY")
        if not api_key:
            raise ValueError("XAI_API_KEY required for xai provider")
        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url="https://api.x.ai/v1",
            temperature=config.llm.temperature,
            max_tokens=max_tokens,
        )
    raise ValueError(f"Unsupported LLM provider: {config.llm.provider}")


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts = [
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        ]
        return "".join(parts)
    return str(content or "")


def _response_usage(response: Any) -> Any:
    """Read token usage however the provider chose to report it."""
    usage = getattr(response, "usage_metadata", None)
    if usage:
        return usage
    meta = getattr(response, "response_metadata", None) or {}
    return meta.get("token_usage") or meta.get("usage")


def _invoke_llm(
    config: AppConfig,
    governor: Governor,
    prompt: str,
    *,
    agent: str,
    phase: str,
    estimated_in: int,
    estimated_out: int,
    lead: str | None = None,
) -> str:
    """Gate, call, book, re-check. The only path to an LLM in this module."""
    governor.check_budget_before_phase(phase, estimated_in, estimated_out)
    governor.note_iteration()

    response = _build_chat_model(config).invoke(prompt)
    text = _response_text(response)
    governor.record_from_usage(
        agent, phase, _response_usage(response), fallback_text=text, lead=lead
    )

    decision = governor.should_stop()
    if not decision.allowed:
        raise GovernorHalt(f"{phase} complete: {decision.reason}")
    return text


def _search(
    config: AppConfig,
    governor: Governor,
    query: str,
    *,
    phase: str,
    tool: Any = None,
) -> str:
    """Gated web search. Public sources only — see ``leadforge.tools``."""
    governor.note_tool_call("web_search", query)
    decision = governor.can_afford(EST_SEARCH_IN, EST_SEARCH_OUT)
    if not decision.allowed:
        raise GovernorHalt(f"{phase} search: {decision.reason}")

    search_tool = tool or build_search_tools(config)[0]
    # ``_run`` is how this repo already invokes CrewAI tools outside an agent
    # loop; see ``leadforge.export._try_sheets_append``.
    result = search_tool._run(query)
    governor.record("web_search", phase, 0, 0, lead=None)
    return result


def _fetch_page(governor: Governor, url: str, tools: list[Any]) -> str:
    """Fetch one public page. Not a paid API, so loop-guarded but not billed."""
    governor.note_tool_call("fetch_public_page", url)
    fetch_tool = next(
        (t for t in tools if getattr(t, "name", "") == "fetch_public_page"), None
    )
    if fetch_tool is None:
        return ""
    return fetch_tool._run(url)


# ---- fallbacks ---------------------------------------------------------


def posting_name_from(company: str) -> str | None:
    """Read the homeowner's posting name out of a household label.

    Only what they published. Returns ``None`` for anything not shaped like a
    household label, so a live result that ignored the format degrades to an
    unnamed lead rather than to a mangled one.
    """
    match = _HOUSEHOLD_LABEL.match((company or "").strip())
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def _research_fallback(lead: DiscoveredLead) -> LeadResearch:
    """Discovery-stage fields promoted to a research record.

    Mirrors the fallback inside ``leadforge.main._research_one_lead`` so a lead
    that fails research still reaches the pitch stage with everything the
    discovery phase already knew.
    """
    return LeadResearch(
        company=lead.company,
        owner_name=posting_name_from(lead.company),
        industry=lead.industry,
        location=lead.location,
        lead_scope=lead.lead_scope,
        pains=lead.pain_signals,
        contact_email=lead.email,
        contact_phone=lead.phone,
        linkedin_url=lead.linkedin_url,
        contact_notes="Research fallback used discovery-stage public contact details.",
        source_urls=lead.source_urls,
        website_summary=lead.discovery_notes or "Research incomplete.",
    )


def _without_source_prefix(signal: str) -> str:
    """Drop a leading ``Public board post:``-style provenance label."""
    text = (signal or "").strip()
    head, separator, tail = text.partition(":")
    if separator and head.lower().startswith("public") and tail.strip():
        return tail.strip()
    return text


def _demand_fallback_pitch(research: LeadResearch) -> PitchOutput:
    """A complete, deterministic shop-to-homeowner draft when the LLM is down.

    Mirrors the guarantees of ``leadforge.main._fallback_pitch`` — every export
    field populated, confidence low, human review on — but writes the message
    this pipeline actually needs: a local shop replying to a public request,
    not a software pitch to a trades business. Without this, a sample run would
    still produce three rows and every one of them would be addressed to the
    wrong party about the wrong thing.
    """
    trade = research.industry or "trade"
    household = research.company
    greeting = research.owner_name or "there"
    problems = [pain for pain in research.pains if pain]
    problem_summary = (
        "; ".join(problems) if problems else "the problem described in your post"
    )
    # The Pains column keeps its source prefix because a reviewer wants to know
    # where a signal came from. The message to the homeowner does not.
    described = ", ".join(_without_source_prefix(pain) for pain in problems) or (
        "the problem described in your post"
    )

    draft = (
        f"Subject: Re: your public post about {trade} work\n\n"
        f"Hi {greeting},\n\n"
        f"I saw your public post about {described} and wanted to reach "
        f"out. We're a local {trade} shop working in "
        f"{research.location or 'your area'}.\n\n"
        "A first visit would be a look at the unit and the surrounding setup, "
        "a check for anything that would make the repair bigger than it looks, "
        "and a written estimate before any work starts. Most jobs like this are "
        "one visit; if it turns out to need a part we don't carry, we'll say so "
        "on the spot rather than leave you waiting.\n\n"
        "If that's useful, reply with a couple of times that suit you this week "
        "and we'll confirm one. No obligation either way.\n\n"
        "Thanks,\nYour local "
        f"{trade} shop\n\n"
        "(Draft for internal review — confirm the public details and the "
        "homeowner's contact preference before anything is sent.)"
    )

    return PitchOutput(
        company=household,
        owner=research.owner_name or "Unknown",
        email=research.contact_email,
        phone=research.contact_phone,
        linkedin_url=None,
        industry=research.industry,
        location=research.location,
        lead_scope=research.lead_scope or "local",
        pains=problem_summary,
        barren_fit=research.barren_fit_analysis
        or (
            f"Trade matches the request ({trade}), the property is in the "
            "service area, and the work described fits a single diagnostic "
            "visit with an estimate before anything is committed."
        ),
        draft_email=draft,
        pitch_angle=f"Local {trade} shop replying to a public request for help.",
        specific_value_props=[
            "same-week diagnostic visit",
            "written estimate before any work starts",
            "clear answer on parts availability at the visit",
            "one point of contact from booking to completion",
        ],
        recommended_next_step=(
            "Verify the public post is still current and unresolved, confirm the "
            "published contact route, then review this draft before sending."
        ),
        confidence=ConfidenceLevel.LOW,
        human_review_required=True,
        research_sources=research.source_urls,
    )


def _zip_from_lead(lead: DiscoveredLead, config: AppConfig) -> str | None:
    """Pull a zip out of the lead's location text, or fall back to config.

    ``DiscoveredLead`` has no zip field and is not forked to add one, so the
    zip is read back out of the location string the discovery prompt asks for
    in ``City, ST ZIP`` form.
    """
    match = _ZIP_PATTERN.search(lead.location or "")
    if match:
        return match.group(1)
    configured = str(config.icp.get("zip_code") or "").strip()
    return configured or None


def _signal_events_from_leads(
    leads: Sequence[DiscoveredLead],
    config: AppConfig,
    *,
    now: datetime | None = None,
) -> list[SignalEvent]:
    """Turn discovered demand into scoring signals.

    One signal per corroborating public source URL, because separate public
    sources describing the same request is exactly what the "agreeing signals"
    rule counts. A weather-flagged zip adds a ``weather_alert`` event, which
    sets the flag without counting as demand.
    """
    moment = now or datetime.now(timezone.utc)
    events: list[SignalEvent] = []

    for lead in leads:
        zip_code = _zip_from_lead(lead, config)
        if not zip_code:
            logger.warning(
                "No zip for %s; skipping signal emission for that lead.",
                lead.company,
            )
            continue

        urls = list(lead.source_urls[:MAX_SIGNALS_PER_LEAD]) or [""]
        for index, url in enumerate(urls):
            signal = (
                lead.pain_signals[index]
                if index < len(lead.pain_signals)
                else (lead.discovery_notes or lead.company)
            )
            events.append(
                SignalEvent(
                    source=DISCOVERY_SIGNAL_SOURCE,
                    url=url,
                    text=signal,
                    zip=zip_code,
                    trade=lead.industry,
                    observed_at=moment,
                    shop_id=None,
                )
            )

        if weather_trigger_for(zip_code):
            events.append(
                SignalEvent(
                    source=WEATHER_SIGNAL_SOURCE,
                    url="",
                    text=f"Weather fixture reports an active event for {zip_code}.",
                    zip=zip_code,
                    trade=lead.industry,
                    observed_at=moment,
                    shop_id=None,
                )
            )

    return events


# ---- per-lead work -----------------------------------------------------


def _research_one(
    config: AppConfig,
    governor: Governor,
    lead: DiscoveredLead,
    *,
    live: bool,
) -> LeadResearch:
    """Research one lead from public sources, or fall back to what we know."""
    if not live:
        return _research_fallback(lead)

    try:
        tools = build_research_tools(config)
        context: list[str] = []

        findings = _search(
            config,
            governor,
            f"{lead.industry} {lead.location} homeowner needs repair public post",
            phase="research",
            tool=tools[0],
        )
        context.append(f"Public search findings:\n{findings}")

        for url in lead.source_urls[:1]:
            # The public post itself, not a profile of the person who wrote it.
            page = _fetch_page(governor, url, tools)
            if page:
                context.append(f"Public post text:\n{page}")

        prompt = demand_research_prompt(lead) + "\n\n" + "\n\n".join(context)
        raw = _invoke_llm(
            config,
            governor,
            prompt,
            agent="ResearchAgent",
            phase="research",
            estimated_in=EST_RESEARCH_IN,
            estimated_out=EST_RESEARCH_OUT,
            lead=lead.company,
        )
        return LeadResearch.model_validate(extract_json_object(raw))
    except GovernorHalt:
        raise
    except Exception as exc:
        logger.warning("Research failed for %s: %s", lead.company, exc)
        return _research_fallback(lead)


def _pitch_one(
    config: AppConfig,
    governor: Governor,
    research: LeadResearch,
    *,
    live: bool,
) -> PitchOutput:
    """Draft the shop's reply to the homeowner. Human review is forced on, always."""
    pitch: PitchOutput
    if not live:
        pitch = _demand_fallback_pitch(research)
    else:
        try:
            prompt = demand_pitch_prompt(
                research.model_dump_json(), human_review=True
            )
            raw = _invoke_llm(
                config,
                governor,
                prompt,
                agent="PitchStrategistAgent",
                phase="pitch",
                estimated_in=EST_PITCH_IN,
                estimated_out=EST_PITCH_OUT,
                lead=research.company,
            )
            pitch = PitchOutput.model_validate(extract_json_object(raw))
        except GovernorHalt:
            raise
        except Exception as exc:
            logger.warning("Pitch failed for %s: %s", research.company, exc)
            pitch = _demand_fallback_pitch(research)

    # Set after validation, so model output that tried to turn it off cannot.
    pitch.human_review_required = True
    return pitch


# ---- nodes -------------------------------------------------------------


def preflight_node(state: ResearchState) -> dict[str, Any]:
    result = run_preflight(state["config"])
    for warning in result.warnings:
        logger.warning("Preflight: %s", warning)

    if result.ok:
        return {"preflight_ok": True, "live": True}

    reason = "preflight: " + "; ".join(result.errors)
    if state.get("sample_mode"):
        # A demo cannot dead-end on a missing key. Seeds and the fallback pitch
        # still produce three complete rows.
        logger.warning("%s — sample mode continuing with seeds and fallbacks", reason)
        return {"preflight_ok": False, "live": False}

    return {"preflight_ok": False, "live": False, "stopped_early": True,
            "stop_reason": reason}


def _budget_gate(
    phase: str, estimated_in: int, estimated_out: int
) -> Callable[[ResearchState], dict[str, Any]]:
    """A governor node: refuse the phase before it starts, never after."""

    def gate(state: ResearchState) -> dict[str, Any]:
        try:
            state["governor"].check_budget_before_phase(
                phase, estimated_in, estimated_out
            )
        except GovernorHalt as halt:
            return {"stopped_early": True, "stop_reason": halt.stop_reason}
        return {}

    return gate


governor_discovery_node = _budget_gate(
    "discovery", EST_DISCOVERY_IN, EST_DISCOVERY_OUT
)


def governor_research_node(state: ResearchState) -> dict[str, Any]:
    """Project every remaining research *and* pitch call before starting."""
    leads = state.get("leads") or []
    try:
        state["governor"].check_remaining_pipeline(
            "research-queue",
            leads_left_research=len(leads),
            leads_left_pitch=len(leads),
        )
    except GovernorHalt as halt:
        return {"stopped_early": True, "stop_reason": halt.stop_reason}
    return {}


def governor_pitch_node(state: ResearchState) -> dict[str, Any]:
    research = state.get("research") or []
    try:
        state["governor"].check_remaining_pipeline(
            "pitch-queue", leads_left_pitch=len(research)
        )
    except GovernorHalt as halt:
        return {"stopped_early": True, "stop_reason": halt.stop_reason}
    return {}


def discover_node(state: ResearchState) -> dict[str, Any]:
    config: AppConfig = state["config"]
    governor: Governor = state["governor"]
    sample_mode = bool(state.get("sample_mode"))
    leads: list[DiscoveredLead] = []

    if state.get("live"):
        try:
            location = resolve_location(config)
            industries = resolve_industries(config)
            trade = industries[0] if industries else "HVAC"
            # Demand-side: the homeowner asking, not the shop selling.
            query = (
                f'"need a {trade}" OR "looking for a {trade}" {location} '
                "homeowner public post"
            )
            findings = _search(config, governor, query, phase="discovery")
            prompt = (
                demand_discovery_prompt(config)
                + f"\n\nPublic search findings:\n{findings}"
            )
            raw = _invoke_llm(
                config,
                governor,
                prompt,
                agent="LeadDiscoveryAgent",
                phase="discovery",
                estimated_in=EST_DISCOVERY_IN,
                estimated_out=EST_DISCOVERY_OUT,
            )
            for item in extract_json_array(raw):
                try:
                    leads.append(DiscoveredLead.model_validate(item))
                except ValidationError as exc:
                    logger.warning("Skipping invalid lead: %s", exc)
        except GovernorHalt as halt:
            return {"stopped_early": True, "stop_reason": halt.stop_reason}
        except Exception as exc:
            if not sample_mode:
                logger.error("Discovery failed: %s", exc)
                raise
            logger.warning("Discovery failed in sample mode (%s); using seeds.", exc)

    if sample_mode:
        target = max(1, config.guardrails.max_leads_processed)
        if len(leads) < target:
            seen = {lead.company.strip().lower() for lead in leads}
            # Demand seeds: fictional homeowners, not the prototype's shops.
            for seed in sample_demand_leads():
                if len(leads) >= target:
                    break
                if seed.company.strip().lower() not in seen:
                    leads.append(seed)
            logger.info("Sample mode: %s household(s) after seed top-up.", len(leads))

    if not leads:
        raise RuntimeError("Discovery returned zero valid leads.")
    return {"leads": governor.cap_leads(leads)}


def research_node(state: ResearchState) -> dict[str, Any]:
    config: AppConfig = state["config"]
    governor: Governor = state["governor"]
    leads: list[DiscoveredLead] = state.get("leads") or []
    live = bool(state.get("live"))

    results: list[LeadResearch] = []
    stop_reason: str | None = None
    if not leads:
        return {"research": results}

    workers = governor.max_parallel_research()
    pending: dict[Future[LeadResearch], DiscoveredLead] = {}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="nf-research") as pool:
        for idx, lead in enumerate(leads):
            if governor.stopped:
                stop_reason = stop_reason or governor.stop_reason
                break
            remaining = len(leads) - idx
            try:
                # Stop queueing work the run can no longer afford to finish.
                governor.check_remaining_pipeline(
                    "research-queue",
                    leads_left_research=remaining,
                    leads_left_pitch=remaining,
                )
            except GovernorHalt as halt:
                stop_reason = halt.stop_reason
                break
            pending[pool.submit(_research_one, config, governor, lead, live=live)] = lead

        for future in as_completed(pending):
            lead = pending[future]
            try:
                results.append(future.result())
            except GovernorHalt as halt:
                stop_reason = stop_reason or halt.stop_reason
            except Exception as exc:
                logger.exception("Unexpected research error for %s: %s", lead.company, exc)

    out: dict[str, Any] = {"research": results}
    if stop_reason:
        out |= {"stopped_early": True, "stop_reason": stop_reason}
    return out


def pitch_node(state: ResearchState) -> dict[str, Any]:
    config: AppConfig = state["config"]
    governor: Governor = state["governor"]
    research_list: list[LeadResearch] = state.get("research") or []
    live = bool(state.get("live"))

    pitches: list[PitchOutput] = []
    stop_reason: str | None = None

    for idx, research in enumerate(research_list):
        if governor.stopped:
            stop_reason = stop_reason or governor.stop_reason
            break
        remaining = len(research_list) - idx
        try:
            governor.check_remaining_pipeline("pitch-queue", leads_left_pitch=remaining)
            pitches.append(_pitch_one(config, governor, research, live=live))
        except GovernorHalt as halt:
            stop_reason = halt.stop_reason
            break

    out: dict[str, Any] = {"pitches": pitches}
    if stop_reason:
        out |= {"stopped_early": True, "stop_reason": stop_reason}
    return out


def halt_node(state: ResearchState) -> dict[str, Any]:
    """A halt is a clean stop. Record why, then fall through to export."""
    governor: Governor = state["governor"]
    reason = state.get("stop_reason") or governor.stop_reason or "Run halted"
    logger.warning("Research run halted: %s", reason)
    return {"stopped_early": True, "stop_reason": reason}


def export_node(state: ResearchState) -> dict[str, Any]:
    config: AppConfig = state["config"]
    governor: Governor = state["governor"]
    manifest: RunManifest = state["manifest"]
    pitches: list[PitchOutput] = state.get("pitches") or []

    manifest.leads_discovered = len(state.get("leads") or [])
    manifest.leads_researched = len(state.get("research") or [])
    manifest.leads_pitched = len(pitches)
    manifest.estimated_cost_usd = round(governor.total_cost_usd(), 4)
    summary = governor.summary()
    manifest.token_usage = {
        "input": summary["total_input_tokens"],
        "output": summary["total_output_tokens"],
    }
    manifest.stopped_early = bool(state.get("stopped_early")) or governor.stopped
    manifest.stop_reason = state.get("stop_reason") or governor.stop_reason
    manifest.human_review_required = True

    paths: dict[str, str] = {}
    if pitches:
        paths = {
            key: str(value)
            for key, value in export_pitches(pitches, manifest, config).items()
        }
    elif state.get("research"):
        logger.warning("No pitches to export (budget stop or errors).")

    token_log = governor.write_log(manifest.run_id, out_dir=output_dir(config))

    return {
        "export_paths": paths,
        "summary": {
            "run_id": manifest.run_id,
            "mode": "sample" if state.get("sample_mode") else "production",
            "leads_discovered": manifest.leads_discovered,
            "leads_researched": manifest.leads_researched,
            "leads_pitched": manifest.leads_pitched,
            "estimated_cost_usd": manifest.estimated_cost_usd,
            "stopped_early": manifest.stopped_early,
            "stop_reason": manifest.stop_reason,
            "human_review_required": True,
            "export_paths": paths,
            "token_log": str(token_log),
            "cost_summary": summary,
        },
    }


def emit_signals_node(state: ResearchState) -> dict[str, Any]:
    """Feed the discovered demand into scoring, after export.

    This is what makes ``research --sample`` and ``score --sample`` describe
    one household set rather than two that merely resemble each other: sample
    mode emits the shared demo events, so both commands band the same three
    people identically.

    Failure here is not allowed to lose a completed export. The CSV is already
    on disk by the time this runs, so a scoring problem is logged and the run
    still reports success.
    """
    config: AppConfig = state["config"]
    leads = state.get("leads") or []
    summary = dict(state.get("summary") or {})

    if state.get("sample_mode"):
        events = sample_demand_events()
        service_area, zone_map = SAMPLE_SERVICE_AREA_ZIPS, SAMPLE_ZONE_MAP
    else:
        events = _signal_events_from_leads(leads, config)
        # No service area configured for a live run, which counts as in-area
        # rather than silently dropping everything.
        service_area, zone_map = None, None

    if not events:
        return {"summary": summary}

    try:
        result = ingest_signals(
            events,
            service_area_zips=service_area,
            zone_map=zone_map,
            output_directory=output_dir(config),
        )
    except Exception as exc:  # noqa: BLE001 - the export already succeeded
        logger.warning("Scoring ingest failed after export: %s", exc)
        return {"summary": summary}

    summary["signal_bands"] = result.band_counts()
    summary["scored_artifact"] = str(result.artifact_path)
    summary["scored_lead_ids"] = result.lead_ids
    logger.info(
        "Scored %s lead(s) from discovery signals: %s",
        len(result.leads),
        result.band_counts(),
    )
    return {"summary": summary, "scored_leads": result.leads}


# ---- graph -------------------------------------------------------------


def _route_or_halt(next_node: str) -> Callable[[ResearchState], str]:
    def router(state: ResearchState) -> str:
        return "halt" if state.get("stopped_early") else next_node

    return router


def build_research_graph() -> StateGraph:
    """Assemble the graph. Call ``.compile()`` to run it."""
    graph = StateGraph(ResearchState)

    graph.add_node("preflight", preflight_node)
    graph.add_node("governor_discovery", governor_discovery_node)
    graph.add_node("discover", discover_node)
    graph.add_node("governor_research", governor_research_node)
    graph.add_node("research", research_node)
    graph.add_node("governor_pitch", governor_pitch_node)
    graph.add_node("pitch", pitch_node)
    graph.add_node("halt", halt_node)
    graph.add_node("export", export_node)
    graph.add_node("emit_signals", emit_signals_node)

    graph.add_edge(START, "preflight")
    for source, target in (
        ("preflight", "governor_discovery"),
        ("governor_discovery", "discover"),
        ("discover", "governor_research"),
        ("governor_research", "research"),
        ("research", "governor_pitch"),
        ("governor_pitch", "pitch"),
        ("pitch", "export"),
    ):
        graph.add_conditional_edges(
            source, _route_or_halt(target), {target: target, "halt": "halt"}
        )

    # Partial work still exports.
    graph.add_edge("halt", "export")
    graph.add_edge("export", "emit_signals")
    graph.add_edge("emit_signals", END)
    return graph


def compile_research_graph() -> Any:
    return build_research_graph().compile()


def run_research(
    options: RunOptions | None = None,
    *,
    config: AppConfig | None = None,
    governor: Governor | None = None,
    run_id: str | None = None,
    output_directory: Path | str | None = None,
) -> dict[str, Any]:
    """Run the graph once and return the run summary."""
    options = options or RunOptions()
    base = config or get_config()
    if output_directory is not None:
        base = base.model_copy(
            update={"export": {**base.export, "output_dir": str(output_directory)}}
        )
    resolved = options.apply_to_config(base)

    governor = governor or Governor.for_research(
        resolved,
        limits=default_loop_limits(resolved.guardrails.max_leads_processed),
    )

    manifest = RunManifest(
        run_id=run_id or RunManifest.new_run_id(),
        started_at=datetime.now(timezone.utc).isoformat(),
        location=resolve_location(resolved),
        industries=resolve_industries(resolved),
        human_review_required=True,
    )

    logger.info(
        "NightForge research run %s | mode=%s | location=%s | model=%s | max_leads=%s",
        manifest.run_id,
        "sample" if options.sample_mode else "production",
        manifest.location,
        resolved.llm.model,
        resolved.guardrails.max_leads_processed,
    )

    final = compile_research_graph().invoke(
        {
            "run_id": manifest.run_id,
            "sample_mode": options.sample_mode,
            "config": resolved,
            "governor": governor,
            "manifest": manifest,
        }
    )
    return final["summary"]
