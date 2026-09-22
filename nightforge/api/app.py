"""The NightForge FastAPI application.

Routes: a health probe, research run submission and lookup, signal ingest and
the ranked lead list, and an inbound Jobber webhook receiver.

What is deliberately absent: there is no send route, no OAuth route, and no
route that writes to a vendor. The webhook receiver verifies, stores, and
stops. A human opens the stored file.

Import stays light at module scope — no graphs, no vendor SDKs, no LLM
clients — so a health check can answer while the rest of the system is cold.
The research graph is imported inside the handler that needs it.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from leadforge.export import output_dir
from leadforge.models import RunManifest
from leadforge.run_context import RunOptions
from nightforge import __version__
from nightforge.adapters.base import AdapterError
from nightforge.adapters.jobber import JobberAdapter
from nightforge.config import leadforge_config
from nightforge.scoring import outcomes
from nightforge.scoring.pipeline import ingest as ingest_signals
from nightforge.scoring.pipeline import rank as rank_leads
from nightforge.scoring.schema import ScoredLead, SignalEvent
from nightforge.scoring.store import OutcomeStore

logger = logging.getLogger(__name__)

# For this milestone only: a fixed header stands in for a real Jobber
# signature, so the accept path is testable before the adapter exists. It is
# not a credential and it is not a secret — the real check is
# ``JobberAdapter.verify_webhook``, which currently fails closed.
STUB_SIGNATURE_HEADER = "X-NightForge-Stub"
STUB_SIGNATURE_VALUE = "allow"

_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_-]")

app = FastAPI(
    title="NightForge",
    version=__version__,
    description="Overnight lead research and inbound triage. No send path.",
    # FastAPI mounts a Swagger OAuth redirect helper by default. Nothing here
    # speaks OAuth, so the route should not exist at all.
    swagger_ui_oauth2_redirect_url=None,
)


# ---- run store ---------------------------------------------------------


@dataclass
class RunRecord:
    """One submitted run. In-memory; a restart forgets history."""

    run_id: str
    status: str  # running | completed | halted | failed
    mode: str = "sample"
    artifact_paths: dict[str, str] = field(default_factory=dict)
    stop_reason: str | None = None
    leads_pitched: int = 0
    estimated_cost_usd: float = 0.0
    submitted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "status": self.status,
            "mode": self.mode,
            "artifact_paths": self.artifact_paths,
            "leads_pitched": self.leads_pitched,
            "estimated_cost_usd": self.estimated_cost_usd,
            "submitted_at": self.submitted_at,
            "human_review_required": True,
        }
        if self.stop_reason:
            payload["stop_reason"] = self.stop_reason
        return payload


_RUNS: dict[str, RunRecord] = {}
_RUNS_LOCK = threading.Lock()

# Scored leads from every ingest so far, newest write wins per lead id.
# In-memory like the run store; a restart forgets them, and the JSON artifact
# on disk is the durable copy.
_LEADS: dict[str, ScoredLead] = {}
_LEADS_LOCK = threading.Lock()


def _allocate_run(mode: str) -> RunRecord:
    """Reserve a run id up front so a lookup during the run returns ``running``.

    ``RunManifest.new_run_id`` is second-granular, so two runs submitted in the
    same second would otherwise collide in the store and overwrite each other.
    """
    base = RunManifest.new_run_id()
    with _RUNS_LOCK:
        run_id = base
        while run_id in _RUNS:
            run_id = f"{base}-{uuid4().hex[:4]}"
        record = RunRecord(run_id=run_id, status="running", mode=mode)
        _RUNS[run_id] = record
    return record


def _store(record: RunRecord) -> None:
    with _RUNS_LOCK:
        _RUNS[record.run_id] = record


def _governor_status() -> str:
    """``halted`` when the most recent finished run hit the governor.

    There is no single long-lived governor — each run gets its own — so this
    reports the last outcome rather than a live instance.
    """
    with _RUNS_LOCK:
        for record in reversed(list(_RUNS.values())):
            if record.status in {"completed", "halted", "failed"}:
                return "halted" if record.status == "halted" else "ready"
    return "ready"


def _output_dir() -> Path:
    """Where runs and inbound events are written. One seam, so tests can move it."""
    return output_dir(leadforge_config())


def _outcome_store() -> OutcomeStore:
    """The append-only outcome log. One seam, so tests can move it."""
    return OutcomeStore()


# ---- request models ----------------------------------------------------


class ResearchRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sample: bool = False
    zip_code: str | None = Field(default=None, alias="zip")
    budget_usd: float | None = None


class SignalIngestRequest(BaseModel):
    shop_id: str | None = None
    events: list[SignalEvent] = Field(default_factory=list)


class LeadDecisionRequest(BaseModel):
    pursued: bool


# ---- routes ------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness probe. Touches nothing external.

    ``status`` and ``version`` predate the governor and adapter fields and are
    kept so existing probes do not break.
    """
    return {
        "ok": True,
        "status": "ok",
        "version": __version__,
        "governor": _governor_status(),
        "adapter": "jobber-stub",
    }


@app.post("/runs/research")
def create_research_run(request: ResearchRunRequest) -> dict[str, Any]:
    """Run the research graph and return its artifacts.

    Synchronous: FastAPI runs this in a worker thread, and a sample run takes
    well under a second. The governor built inside ``run_research`` gates every
    LLM and paid search call, and a halt is a normal result — status ``halted``
    with a ``stop_reason``, not a 500.
    """
    from nightforge.graphs.research import run_research

    record = _allocate_run("sample" if request.sample else "production")
    options = RunOptions(
        sample_mode=request.sample,
        target_zip=request.zip_code,
        budget_usd=request.budget_usd,
    )

    try:
        summary = run_research(
            options, run_id=record.run_id, output_directory=_output_dir()
        )
    except Exception as exc:  # noqa: BLE001 - a failed run is a result, not a 500
        logger.exception("Research run %s failed", record.run_id)
        record.status = "failed"
        record.stop_reason = f"{type(exc).__name__}: {exc}"
        _store(record)
        return record.to_payload()

    record.status = "halted" if summary["stopped_early"] else "completed"
    record.stop_reason = summary["stop_reason"]
    record.leads_pitched = summary["leads_pitched"]
    record.estimated_cost_usd = summary["estimated_cost_usd"]
    record.artifact_paths = {
        **summary["export_paths"],
        "token_log": summary["token_log"],
    }
    _store(record)
    return record.to_payload()


@app.get("/runs/{run_id}")
def get_research_run(run_id: str) -> dict[str, Any]:
    with _RUNS_LOCK:
        record = _RUNS.get(run_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown run {run_id}"
        )
    return record.to_payload()


# ---- signals and leads -------------------------------------------------


@app.post("/ingest/signals")
def ingest_signal_events(request: SignalIngestRequest) -> dict[str, Any]:
    """Score a batch of public demand signals into ranked leads.

    Deterministic and offline: no LLM, no model, no vendor call. The rules live
    in ``nightforge.scoring.heuristic`` and every lead carries the reasons it
    was banded the way it was.
    """
    result = ingest_signals(
        request.events,
        shop_id=request.shop_id,
        output_directory=_output_dir(),
    )

    with _LEADS_LOCK:
        for lead in result.leads:
            _LEADS[lead.lead_id] = lead

    return {
        "lead_ids": result.lead_ids,
        "artifact_path": str(result.artifact_path),
        "band_counts": result.band_counts(),
        "human_review_required": True,
    }


@app.get("/leads")
def list_leads() -> list[dict[str, Any]]:
    """Every scored lead, ranked hot then warm then log.

    Warm is not filtered out. It is the band where a human's judgement
    actually changes the outcome.
    """
    with _LEADS_LOCK:
        leads = list(_LEADS.values())
    return [lead.model_dump(mode="json") for lead in rank_leads(leads)]


@app.post("/leads/{lead_id}/decision")
def record_lead_decision(
    lead_id: str, request: LeadDecisionRequest
) -> dict[str, Any]:
    """Record that a human accepted or rejected this lead.

    Half a training label. The other half arrives later, if the job it became
    closes won or lost. Recording only — nothing is contacted and no work is
    booked.
    """
    with _LEADS_LOCK:
        lead = _LEADS.get(lead_id)
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown lead {lead_id}"
        )

    record = outcomes.record_decision(
        lead_id,
        request.pursued,
        store=_outcome_store(),
        shop_id=lead.shop_id,
        unit=lead.suggested_unit,
    )
    return {
        "recorded": True,
        "human_review_required": True,
        **record.model_dump(mode="json"),
    }


# ---- inbound webhook ---------------------------------------------------


def _safe_event_id(raw: Any) -> str:
    """Filesystem-safe id.

    The id comes from a request body, so it is scrubbed to an allow-list before
    it reaches a path. Anything else is a directory traversal waiting to happen.
    """
    cleaned = _UNSAFE_ID.sub("-", str(raw or "")).strip("-")[:64]
    return cleaned or uuid4().hex


def _verify_inbound(payload: bytes, headers: Mapping[str, str]) -> str | None:
    """Return how the payload was verified, or ``None`` to reject.

    Fails closed: the adapter is unimplemented, so anything without the stub
    header is rejected rather than waved through.
    """
    if headers.get(STUB_SIGNATURE_HEADER) == STUB_SIGNATURE_VALUE:
        return "stub-header"
    try:
        if JobberAdapter().verify_webhook(payload, headers):
            return "adapter"
    except (NotImplementedError, AdapterError) as exc:
        logger.warning("Jobber webhook verification unavailable: %s", exc)
    return None


@app.post("/webhooks/jobber", status_code=status.HTTP_202_ACCEPTED)
async def jobber_webhook(request: Request) -> dict[str, Any]:
    """Accept a verified Jobber event, store it, and stop.

    Nothing is sent, nothing is written back to Jobber, and no reply is
    drafted. The event lands on disk for a human to open.
    """
    payload = await request.body()
    verified_by = _verify_inbound(payload, request.headers)
    if verified_by is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook signature could not be verified",
        )

    try:
        body = json.loads(payload) if payload else {}
    except ValueError:
        body = {"raw": payload.decode("utf-8", errors="replace")}

    event_id = _safe_event_id(
        (body.get("id") or body.get("event_id")) if isinstance(body, dict) else None
    )
    path = _output_dir() / f"inbound_{event_id}.json"
    path.write_text(
        json.dumps(
            {
                "event_id": event_id,
                "source": "jobber",
                "received_at": datetime.now(timezone.utc).isoformat(),
                "verified_by": verified_by,
                "disposition": "stored_for_human_review",
                "human_review_required": True,
                "payload": body,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    logger.info("Stored inbound Jobber event %s at %s", event_id, path)

    # A status change carrying won/lost is the delayed half of a training
    # label. Reading it needs no adapter call: everything used is already in
    # the verified payload, so hydrate, find_or_create_client,
    # create_request_or_job, and place_draft stay untouched.
    outcome = outcomes.attach_job_outcome(
        body if isinstance(body, dict) else {},
        store=_outcome_store(),
        event_id=event_id,
    )

    response: dict[str, Any] = {
        "status": "accepted",
        "event_id": event_id,
        "stored_at": str(path),
        "disposition": "stored_for_human_review",
        "human_review_required": True,
        "outcome_recorded": outcome is not None,
    }
    if outcome is not None:
        response["outcome"] = outcome.model_dump(mode="json")
    return response
