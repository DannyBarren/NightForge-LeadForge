"""
LeadForge orchestration — discovery → parallel research → pitch → export.

Designed for cost-controlled runs under a hard $10 ceiling with token logging.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

from crewai import Crew, Process
from dotenv import load_dotenv
from pydantic import ValidationError

from leadforge.agents import (
    lead_discovery_agent,
    pitch_strategist_agent,
    research_agent,
)
from leadforge.config_loader import _project_root, get_config, resolve_industries, resolve_location
from leadforge.cost_tracker import CostTracker
from leadforge.export import export_pitches
from leadforge.guardrails import (
    EST_DISCOVERY_IN,
    EST_DISCOVERY_OUT,
    EST_PITCH_IN,
    EST_PITCH_OUT,
    EST_RESEARCH_IN,
    EST_RESEARCH_OUT,
    GuardrailViolation,
    Guardrails,
)
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
from leadforge.tasks import discovery_task, pitch_task, research_task

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, str, dict[str, Any] | None], None]


def _estimate_tokens_from_text(text: str) -> tuple[int, int]:
    chars = len(text or "")
    return max(chars // 4, 200), max(chars // 4, 200)




def _emit_progress(
    callback: ProgressCallback | None,
    phase: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    if callback:
        callback(phase, message, data)


def _parse_crew_usage(result: Any, crew: Crew) -> Any:
    for obj in (result, crew):
        for attr in ("token_usage", "usage_metrics", "cost_usage"):
            usage = getattr(obj, attr, None)
            if usage:
                return usage
    if hasattr(result, "raw") and isinstance(result.raw, dict):
        return result.raw.get("token_usage")
    return None


def _run_crew(
    crew: Crew,
    phase: str,
    agent_name: str,
    cost: CostTracker,
    *,
    lead: str | None = None,
) -> str:
    ok, reason = cost.can_afford(500, 500)
    if not ok:
        raise GuardrailViolation(f"{phase}: {reason}")

    result = crew.kickoff()
    raw = str(result)
    usage = _parse_crew_usage(result, crew)
    if usage:
        cost.record_from_crew_usage(agent_name, phase, usage, lead=lead)
    else:
        inp, out = _estimate_tokens_from_text(raw)
        cost.record(agent_name, phase, inp, out, lead=lead)

    stop, reason = cost.should_stop()
    if stop:
        raise GuardrailViolation(f"{phase} complete: {reason}")
    return raw


def run_discovery(
    config,
    guardrails: Guardrails,
    cost: CostTracker,
    *,
    sample_mode: bool = False,
) -> list[DiscoveredLead]:
    guardrails.check_budget_before_phase(
        "discovery", EST_DISCOVERY_IN, EST_DISCOVERY_OUT
    )

    leads: list[DiscoveredLead] = []
    try:
        agent = lead_discovery_agent(config)
        task = discovery_task(agent, config)
        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            verbose=True,
        )
        raw = _run_crew(crew, "discovery", "LeadDiscoveryAgent", cost)
        items = extract_json_array(raw)
        for item in items:
            try:
                leads.append(DiscoveredLead.model_validate(item))
            except ValidationError as ve:
                logger.warning("Skipping invalid lead: %s", ve)
    except GuardrailViolation:
        raise
    except Exception as e:
        # In sample mode we never let a discovery failure kill the demo — we
        # fall back to built-in seed leads below.
        if not sample_mode:
            logger.error("Discovery failed: %s", e)
            raise
        logger.warning("Discovery failed in sample mode (%s); using seed leads.", e)

    if sample_mode:
        target = max(1, config.guardrails.max_leads_processed)
        if len(leads) < target:
            from leadforge.sample_data import sample_seed_leads

            existing = {lead.company.strip().lower() for lead in leads}
            for seed in sample_seed_leads():
                if len(leads) >= target:
                    break
                if seed.company.strip().lower() not in existing:
                    leads.append(seed)
            logger.info(
                "Sample mode: %s lead(s) ready after seed top-up.", len(leads)
            )

    if not leads:
        raise RuntimeError("Discovery returned zero valid leads.")
    return guardrails.cap_leads(leads)


def _research_one_lead(
    config,
    lead: DiscoveredLead,
    cost: CostTracker,
) -> LeadResearch:
    ok, reason = cost.can_afford(EST_RESEARCH_IN, EST_RESEARCH_OUT)
    if not ok:
        raise GuardrailViolation(f"research @{lead.company}: {reason}")

    agent = research_agent(config)
    task = research_task(agent, lead)
    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )
    try:
        raw = _run_crew(
            crew, "research", "ResearchAgent", cost, lead=lead.company
        )
        data = extract_json_object(raw)
        return LeadResearch.model_validate(data)
    except GuardrailViolation:
        raise
    except Exception as e:
        logger.warning("Research failed for %s: %s", lead.company, e)
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


def run_parallel_research(
    config,
    guardrails: Guardrails,
    cost: CostTracker,
    leads: list[DiscoveredLead],
) -> list[LeadResearch]:
    if not leads:
        return []

    results: list[LeadResearch] = []
    workers = guardrails.max_parallel_research()
    pending: dict[Future[LeadResearch], DiscoveredLead] = {}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="research") as pool:
        for idx, lead in enumerate(leads):
            if guardrails.stopped or guardrails.sync_stop_from_cost():
                break

            remaining_r = len(leads) - idx
            remaining_p = len(leads) - idx
            try:
                guardrails.check_remaining_pipeline(
                    "research-queue",
                    leads_left_research=remaining_r,
                    leads_left_pitch=remaining_p,
                )
            except GuardrailViolation as gv:
                guardrails.mark_stopped(str(gv))
                break

            pending[pool.submit(_research_one_lead, config, lead, cost)] = lead

        for fut in as_completed(pending):
            lead = pending[fut]
            try:
                research = fut.result()
                results.append(research)
            except GuardrailViolation as gv:
                guardrails.mark_stopped(str(gv))
                break
            except Exception as e:
                logger.exception("Unexpected research error for %s: %s", lead.company, e)

            if guardrails.sync_stop_from_cost():
                break

    logger.info("Research complete: %s/%s leads", len(results), len(leads))
    return results


def _pitch_one(
    config,
    guardrails: Guardrails,
    cost: CostTracker,
    research: LeadResearch,
) -> PitchOutput:
    guardrails.check_budget_before_phase("pitch", EST_PITCH_IN, EST_PITCH_OUT)

    agent = pitch_strategist_agent(config)
    task = pitch_task(
        agent,
        research.model_dump_json(),
        human_review=guardrails.human_review_required(),
    )
    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )
    try:
        raw = _run_crew(
            crew, "pitch", "PitchStrategistAgent", cost, lead=research.company
        )
        data = extract_json_object(raw)
        pitch = PitchOutput.model_validate(data)
        pitch.human_review_required = True
        return pitch
    except GuardrailViolation:
        raise
    except Exception as e:
        logger.warning("Pitch failed for %s: %s", research.company, e)
        return _fallback_pitch(research)


def _fallback_pitch(research: LeadResearch) -> PitchOutput:
    """Deterministic, complete pitch used when the LLM pitch step fails.

    Guarantees every export row is fully populated (including a usable draft
    email) and flagged for human review.
    """
    owner = research.owner_name or "there"
    company = research.company
    pains = [p for p in research.pains if p]
    pain_summary = ", ".join(pains) if pains else (
        "admin overload from scheduling, missed calls, estimates, and invoicing"
    )
    draft_email = (
        f"Subject: A quick idea for {company}\n\n"
        f"Hi {owner},\n\n"
        f"I came across {company} while researching {research.industry or 'local trade'} "
        f"businesses in {research.location or 'your area'}, and a few public signals stood "
        f"out — {pain_summary}. Growing trade shops usually hit the same wall: the phone, "
        f"scheduling, estimates, invoicing, and compliance paperwork start eating the hours "
        f"you'd rather spend on the work itself.\n\n"
        f"That's exactly what Barren handles. We automate the back office — missed-call "
        f"capture and follow-up, online booking and intake, estimate and invoice reminders, "
        f"and compliance paperwork — so you keep more jobs on the calendar without adding "
        f"headcount. Most owners we work with stop losing after-hours calls and cut the time "
        f"they spend chasing paperwork each week.\n\n"
        f"Would you be open to a short, no-pressure look at how this could fit {company}? "
        f"If it's helpful I can send a one-page summary first — no call required.\n\n"
        f"Thanks for the time,\nBarren Business Development\n\n"
        f"(Draft for internal review — verify all public details before any outreach.)"
    )
    return PitchOutput(
        company=company,
        owner=research.owner_name or "Unknown",
        email=research.contact_email,
        phone=research.contact_phone,
        linkedin_url=research.linkedin_url,
        industry=research.industry,
        location=research.location,
        lead_scope=research.lead_scope,
        pains=pain_summary,
        barren_fit=research.barren_fit_analysis
        or (
            "Barren can automate scheduling, missed-call capture and follow-up, "
            "estimate and invoice reminders, and compliance paperwork so the owner "
            "spends more time on the trade and less on admin."
        ),
        draft_email=draft_email,
        pitch_angle="Reduce admin burden so you can focus on the trade.",
        specific_value_props=[
            "missed-call capture and follow-up",
            "online booking and intake automation",
            "estimate and invoice follow-up",
            "compliance paperwork automation",
        ],
        recommended_next_step=(
            "Verify contact details and review sources before sending any outreach."
        ),
        confidence=ConfidenceLevel.LOW,
        human_review_required=True,
        research_sources=research.source_urls,
    )


def run_pitches(
    config,
    guardrails: Guardrails,
    cost: CostTracker,
    research_list: list[LeadResearch],
) -> list[PitchOutput]:
    pitches: list[PitchOutput] = []
    for idx, research in enumerate(research_list):
        if guardrails.stopped or guardrails.sync_stop_from_cost():
            break
        remaining = len(research_list) - idx
        try:
            guardrails.check_remaining_pipeline(
                "pitch-queue", leads_left_pitch=remaining
            )
            pitch = _pitch_one(config, guardrails, cost, research)
            pitches.append(pitch)
        except GuardrailViolation as gv:
            guardrails.mark_stopped(str(gv))
            break
    return pitches


def run_pipeline(
    options: RunOptions | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Execute full pipeline; returns summary dict."""
    load_dotenv(_project_root() / ".env")
    options = options or RunOptions()
    base_config = get_config()
    config = options.apply_to_config(base_config)

    _emit_progress(progress_callback, "preflight", "Validating API keys and configuration", None)
    preflight = run_preflight(config)
    for w in preflight.warnings:
        logger.warning("Preflight: %s", w)
    if not preflight.ok:
        raise RuntimeError("Preflight failed: " + "; ".join(preflight.errors))

    cost = CostTracker(config=config, model=config.llm.model)
    guardrails = Guardrails(config, cost)

    run_id = RunManifest.new_run_id()
    manifest = RunManifest(
        run_id=run_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        location=resolve_location(config),
        industries=resolve_industries(config),
        human_review_required=True,
    )

    mode = "sample" if options.sample_mode else "production"
    logger.info(
        "LeadForge run %s | mode=%s | location=%s | model=%s | max_leads=%s",
        run_id,
        mode,
        manifest.location,
        config.llm.model,
        config.guardrails.max_leads_processed,
    )

    leads: list[DiscoveredLead] = []
    research_list: list[LeadResearch] = []
    pitches: list[PitchOutput] = []

    try:
        _emit_progress(
            progress_callback,
            "discovery",
            "Discovering local and remote trades businesses",
            {"target": config.guardrails.discovery_target_min},
        )
        leads = run_discovery(config, guardrails, cost, sample_mode=options.sample_mode)
        manifest.leads_discovered = len(leads)
        _emit_progress(
            progress_callback,
            "discovery_complete",
            f"Discovered {len(leads)} valid leads",
            {"leads_discovered": len(leads)},
        )

        _emit_progress(
            progress_callback,
            "research",
            f"Researching {len(leads)} leads with {guardrails.max_parallel_research()} workers",
            {"leads": len(leads), "workers": guardrails.max_parallel_research()},
        )
        research_list = run_parallel_research(config, guardrails, cost, leads)
        manifest.leads_researched = len(research_list)
        _emit_progress(
            progress_callback,
            "research_complete",
            f"Completed research for {len(research_list)} leads",
            {"leads_researched": len(research_list)},
        )

        _emit_progress(
            progress_callback,
            "pitch",
            f"Writing personalized pitches for {len(research_list)} leads",
            {"leads": len(research_list)},
        )
        pitches = run_pitches(config, guardrails, cost, research_list)
        manifest.leads_pitched = len(pitches)
        _emit_progress(
            progress_callback,
            "pitch_complete",
            f"Generated {len(pitches)} pitch-ready leads",
            {"leads_pitched": len(pitches)},
        )

    except GuardrailViolation as gv:
        manifest.stopped_early = True
        manifest.stop_reason = str(gv)
        logger.warning("Pipeline stopped: %s", gv)
        _emit_progress(progress_callback, "stopped", str(gv), None)
    except Exception:
        logger.exception("Pipeline failed")
        raise

    if guardrails.stopped:
        manifest.stopped_early = True
        manifest.stop_reason = manifest.stop_reason or guardrails.stop_reason

    manifest.estimated_cost_usd = round(cost.total_cost_usd(), 4)
    manifest.token_usage = {"input": cost.total_input, "output": cost.total_output}

    paths: dict[str, str] = {}
    if pitches:
        paths = {k: str(v) for k, v in export_pitches(pitches, manifest, config).items()}
    elif research_list:
        logger.warning("No pitches to export (budget stop or errors).")

    token_log = cost.write_log(run_id)
    cost_summary = cost.summary()
    _emit_progress(
        progress_callback,
        "complete",
        f"Run complete: {manifest.leads_pitched} pitches, estimated ${manifest.estimated_cost_usd:.4f}",
        {
            "leads_pitched": manifest.leads_pitched,
            "estimated_cost_usd": manifest.estimated_cost_usd,
            "budget_remaining_usd": cost_summary["budget_remaining_usd"],
        },
    )

    summary = {
        "run_id": run_id,
        "mode": mode,
        "leads_discovered": manifest.leads_discovered,
        "leads_researched": manifest.leads_researched,
        "leads_pitched": manifest.leads_pitched,
        "estimated_cost_usd": manifest.estimated_cost_usd,
        "stopped_early": manifest.stopped_early,
        "stop_reason": manifest.stop_reason,
        "human_review_required": True,
        "export_paths": paths,
        "token_log": str(token_log),
        "cost_summary": cost_summary,
    }
    logger.info("Run complete: %s", json.dumps(summary, indent=2))

    if options.sample_mode and manifest.leads_pitched > 0:
        csv_path = paths.get("csv") or paths.get("json") or "(no export)"
        logger.info(
            "DEMO READY: %s lead(s) -> %s | est. cost $%.4f | human review required on every row",
            manifest.leads_pitched,
            csv_path,
            manifest.estimated_cost_usd,
        )

    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_pipeline()
