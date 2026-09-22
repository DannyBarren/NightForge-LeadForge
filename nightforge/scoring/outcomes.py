"""Recording what happened: the accept/reject call, and the job result later.

Two writers, one row type. A human accepts or rejects a lead, and months of
jobs later a status change says whether it was won or lost. Both land in the
outcome store as :class:`DecisionLog` rows so training reads one file.

Nothing here contacts anyone or writes to a vendor. It records.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping

from nightforge.scoring.schema import ClosedOutcome, DecisionLog
from nightforge.scoring.store import OutcomeStore

logger = logging.getLogger(__name__)

# Only these spellings count as a result. Anything else is a status change we
# do not understand, and guessing at it would poison the training labels.
WON_VALUES = frozenset({"won", "closed_won"})
LOST_VALUES = frozenset({"lost", "closed_lost"})

STATUS_KEYS = ("closed", "status", "outcome", "state")
NESTED_KEYS = ("data", "job", "request", "event", "payload", "attributes")

UNATTACHED_PREFIX = "unattached:"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_decision(
    lead_id: str,
    pursued: bool,
    *,
    store: OutcomeStore,
    shop_id: str | None = None,
    unit: str | None = None,
    decided_at: datetime | None = None,
) -> DecisionLog:
    """A human accepted or rejected the lead. The first half of a label."""
    return store.append(
        DecisionLog(
            lead_id=lead_id,
            shop_id=shop_id,
            pursued=pursued,
            unit=unit,
            decided_at=decided_at or _now(),
        )
    )


def record_job_result(
    lead_id: str,
    closed: ClosedOutcome | str,
    *,
    store: OutcomeStore,
    revenue: float | None = None,
    unit: str | None = None,
    shop_id: str | None = None,
    decided_at: datetime | None = None,
) -> DecisionLog:
    """The job finished. The delayed half of a label.

    ``pursued`` is forced true: a lead cannot reach won or lost without having
    been worked, so a payload claiming otherwise is normalised here rather than
    stored as a contradiction.
    """
    return store.append(
        DecisionLog(
            lead_id=lead_id,
            shop_id=shop_id,
            pursued=True,
            closed=ClosedOutcome(closed),
            revenue=revenue,
            unit=unit,
            decided_at=decided_at or _now(),
        )
    )


def _scopes(payload: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """The payload itself, then one level of the usual wrapper keys."""
    yield payload
    for key in NESTED_KEYS:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            yield nested


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for scope in _scopes(payload):
        for key in keys:
            if key in scope and scope[key] is not None:
                return scope[key]
    return None


def _as_outcome(value: Any) -> ClosedOutcome | None:
    text = str(value or "").strip().lower()
    if text in WON_VALUES:
        return ClosedOutcome.WON
    if text in LOST_VALUES:
        return ClosedOutcome.LOST
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_job_outcome(
    payload: Mapping[str, Any], *, event_id: str | None = None
) -> DecisionLog | None:
    """Read a job/request status change, or return ``None``.

    ``None`` is the common case and the safe one: most inbound events are not
    results, and a payload we cannot read is left alone rather than guessed at.

    A result with no ``lead_id`` is still kept, under an ``unattached:`` id, so
    a real won job is not discarded just because the webhook did not carry the
    link back. It will not train anything until someone reconciles it, but it
    is not lost either.
    """
    if not isinstance(payload, Mapping):
        return None

    closed = _as_outcome(_first(payload, *STATUS_KEYS))
    if closed is None:
        return None

    lead_id = _first(payload, "lead_id")
    resolved_id = str(lead_id) if lead_id else (
        f"{UNATTACHED_PREFIX}{event_id}" if event_id else None
    )
    if not resolved_id:
        logger.warning("Job result with no lead_id and no event id; skipping.")
        return None

    unit = _first(payload, "unit")
    shop_id = _first(payload, "shop_id")
    return DecisionLog(
        lead_id=resolved_id,
        shop_id=str(shop_id) if shop_id else None,
        pursued=True,
        closed=closed,
        revenue=_as_float(_first(payload, "revenue")),
        unit=str(unit) if unit else None,
        decided_at=_now(),
    )


def attach_job_outcome(
    payload: Mapping[str, Any],
    *,
    store: OutcomeStore,
    event_id: str | None = None,
) -> DecisionLog | None:
    """Extract a result from an inbound payload and store it if there is one."""
    outcome = extract_job_outcome(payload, event_id=event_id)
    if outcome is None:
        return None
    store.append(outcome)
    logger.info(
        "Attached delayed outcome %s for lead %s", outcome.closed, outcome.lead_id
    )
    return outcome
