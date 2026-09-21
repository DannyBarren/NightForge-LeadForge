"""Research graph — the CrewAI pipeline ported to LangGraph.

``preflight → discover → parallel research → pitch → export``, with a governor
gate node in front of each phase that spends money, and a halt node that routes
to export so partial work still lands on disk.

Nothing about the pipeline's contracts is reinvented here. Prompts come from
``leadforge.tasks``, search and page fetch from ``leadforge.tools``, models from
``leadforge.models``, the demo seeds and the deterministic fallback pitch from
``leadforge.sample_data`` and ``leadforge.main``, and the CSV shape from
``leadforge.export.EXPORT_COLUMNS``. The CrewAI prototype keeps working
untouched; this is a second front end over the same parts.

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
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

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

# The deterministic fallback pitch is the reason a sample run cannot dead-end.
# Reused rather than reimplemented so both pipelines degrade identically.
from leadforge.main import _fallback_pitch
from leadforge.models import DiscoveredLead, LeadResearch, PitchOutput, RunManifest
from leadforge.preflight import run_preflight
from leadforge.run_context import RunOptions
from leadforge.sample_data import sample_seed_leads
from leadforge.tasks import discovery_task, pitch_task, research_task
from leadforge.tools import build_research_tools, build_search_tools
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

logger = logging.getLogger(__name__)

# A search request is priced per call, not per token. Until a real per-request
# rate is configured it is gated and booked at the governor's estimate floor —
# approximate, but never free, which is the property that matters for a cap.
EST_SEARCH_IN, EST_SEARCH_OUT = 200, 200


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


def _research_fallback(lead: DiscoveredLead) -> LeadResearch:
    """Discovery-stage fields promoted to a research record.

    Mirrors the fallback inside ``leadforge.main._research_one_lead`` so a lead
    that fails research still reaches the pitch stage with everything the
    discovery phase already knew.
    """
    return LeadResearch(
        company=lead.company,
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
            f"{lead.company} {lead.location} reviews scheduling booking",
            phase="research",
            tool=tools[0],
        )
        context.append(f"Public search findings:\n{findings}")

        if lead.website:
            page = _fetch_page(governor, lead.website, tools)
            if page:
                context.append(f"Company website text:\n{page}")

        prompt = research_task(None, lead).description + "\n\n" + "\n\n".join(context)
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
    """Draft one pitch. Human review is forced on afterwards, always."""
    pitch: PitchOutput
    if not live:
        pitch = _fallback_pitch(research)
    else:
        try:
            prompt = pitch_task(
                None, research.model_dump_json(), human_review=True
            ).description
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
            pitch = _fallback_pitch(research)

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
            query = (
                f"{industries[0] if industries else 'trades'} companies "
                f"{location} contact owner reviews"
            )
            findings = _search(config, governor, query, phase="discovery")
            prompt = (
                discovery_task(None, config).description
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
            for seed in sample_seed_leads():
                if len(leads) >= target:
                    break
                if seed.company.strip().lower() not in seen:
                    leads.append(seed)
            logger.info("Sample mode: %s lead(s) after seed top-up.", len(leads))

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
    graph.add_edge("export", END)
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
