"""Ingest signals, score them, rank them, write them down.

``events → features → heuristic → assigner → JSON on disk``.

The ranked list keeps every band. Warm is the slice most worth a human's
attention — hot is usually obvious and log is usually noise — so nothing here
filters or truncates. Callers that want only the top of the list can slice it
themselves, visibly, rather than having the pipeline decide for them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from leadforge.config_loader import get_config
from leadforge.export import output_dir
from nightforge.scoring import assigner, heuristic
from nightforge.scoring.features import extract_features
from nightforge.scoring.sample_events import (
    SAMPLE_SERVICE_AREA_ZIPS,
    SAMPLE_ZONE_MAP,
    sample_signal_events,
)
from nightforge.scoring.schema import Band, ScoredLead, SignalEvent

BAND_ORDER = {Band.HOT: 0, Band.WARM: 1, Band.LOG: 2}


@dataclass(frozen=True)
class IngestResult:
    """One scoring batch: the ranked leads and where they were written."""

    leads: list[ScoredLead]
    artifact_path: Path
    batch_id: str

    @property
    def lead_ids(self) -> list[str]:
        return [lead.lead_id for lead in self.leads]

    def band_counts(self) -> dict[str, int]:
        counts = {band.value: 0 for band in Band}
        for lead in self.leads:
            counts[lead.band.value] += 1
        return counts


def rank(leads: Iterable[ScoredLead]) -> list[ScoredLead]:
    """Hot, then warm, then log; by score inside a band; then by id.

    The final key is what makes the order reproducible when two leads tie.
    """
    return sorted(
        leads,
        key=lambda lead: (BAND_ORDER[lead.band], -lead.score, lead.lead_id),
    )


def new_batch_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_output_dir(output_directory: Path | str | None) -> Path:
    if output_directory is not None:
        path = Path(output_directory)
        path.mkdir(parents=True, exist_ok=True)
        return path
    return output_dir(get_config())


def score_events(
    events: Sequence[SignalEvent],
    *,
    now: datetime | None = None,
    service_area_zips: Iterable[str] | None = None,
    known_customers: Iterable[str] = (),
    recent_lead_keys: Iterable[str] = (),
    zone_map: Mapping[str, str] | None = None,
    roster: Sequence[assigner.Unit] | None = None,
) -> list[ScoredLead]:
    """Score without persisting. Ranked, every band included."""
    features_by_lead = extract_features(
        events,
        now=now,
        service_area_zips=service_area_zips,
        known_customers=known_customers,
        recent_lead_keys=recent_lead_keys,
    )

    leads: list[ScoredLead] = []
    for lead_id, features in features_by_lead.items():
        verdict = heuristic.evaluate(features)
        zone, unit = assigner.suggest(features, zone_map=zone_map, roster=roster)
        leads.append(
            ScoredLead(
                lead_id=lead_id,
                shop_id=features.shop_id,
                features=features,
                score=verdict.score,
                band=verdict.band,
                confidence_lo=verdict.confidence_lo,
                confidence_hi=verdict.confidence_hi,
                reasons=list(verdict.reasons),
                suggested_zone=zone,
                suggested_unit=unit,
                route_decision=verdict.route_decision,
            )
        )
    return rank(leads)


def persist(
    leads: Sequence[ScoredLead],
    *,
    batch_id: str,
    output_directory: Path | str | None = None,
) -> Path:
    """Write ``scored_<batch_id>.json``."""
    target = _resolve_output_dir(output_directory) / f"scored_{batch_id}.json"
    counts = {band.value: 0 for band in Band}
    for lead in leads:
        counts[lead.band.value] += 1

    target.write_text(
        json.dumps(
            {
                "batch_id": batch_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "lead_count": len(leads),
                "band_counts": counts,
                "human_review_required": True,
                "leads": [lead.model_dump(mode="json") for lead in leads],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def ingest(
    events: Sequence[SignalEvent],
    *,
    shop_id: str | None = None,
    now: datetime | None = None,
    service_area_zips: Iterable[str] | None = None,
    known_customers: Iterable[str] = (),
    recent_lead_keys: Iterable[str] = (),
    zone_map: Mapping[str, str] | None = None,
    roster: Sequence[assigner.Unit] | None = None,
    output_directory: Path | str | None = None,
    batch_id: str | None = None,
) -> IngestResult:
    """Full pass: features, rules, assignment, disk."""
    if shop_id:
        events = [
            event if event.shop_id else event.model_copy(update={"shop_id": shop_id})
            for event in events
        ]

    leads = score_events(
        events,
        now=now,
        service_area_zips=service_area_zips,
        known_customers=known_customers,
        recent_lead_keys=recent_lead_keys,
        zone_map=zone_map,
        roster=roster,
    )
    resolved_batch = batch_id or new_batch_id()
    path = persist(leads, batch_id=resolved_batch, output_directory=output_directory)
    return IngestResult(leads=leads, artifact_path=path, batch_id=resolved_batch)


def run_sample(
    *,
    output_directory: Path | str | None = None,
    now: datetime | None = None,
) -> IngestResult:
    """Score the demo events. Offline, no keys, no LLM."""
    return ingest(
        sample_signal_events(now=now),
        now=now,
        service_area_zips=SAMPLE_SERVICE_AREA_ZIPS,
        zone_map=SAMPLE_ZONE_MAP,
        output_directory=output_directory,
    )
