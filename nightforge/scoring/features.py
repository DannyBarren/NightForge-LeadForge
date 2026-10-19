"""Turn raw signal events into per-lead features.

Events are grouped by ``(shop_id, zip, trade)``. Several signals about the same
trade in the same zip are treated as one lead pushing harder, which is exactly
what the "three agreeing signals" rule is counting. The limitation is the flip
side of that: two unrelated homeowners in one zip needing the same trade merge
into a single lead. Disambiguating them needs an identity signal the public web
does not reliably give us.

Every input that could disqualify a lead — service area, existing customers,
recently seen leads — is passed in by the caller. Nothing is read from a global
or a database, so the same events always produce the same features.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Sequence

from nightforge.scoring.schema import LeadFeatures, SignalEvent

SIGNAL_WINDOW_HOURS = 48
DUPLICATE_WINDOW_DAYS = 30

# A homeowner asking for the work in public. Worth more than a passing mention.
STRONG_SOURCES = frozenset(
    {"community_board_post", "public_qa_thread", "public_review"}
)

# Context, not demand. These set the weather flag and are not counted as
# signals — otherwise every storm would silently promote a one-signal lead.
WEATHER_SOURCES = frozenset({"weather_alert", "storm_advisory"})

_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _slug(value: str) -> str:
    return _SLUG_UNSAFE.sub("-", (value or "").strip().lower()).strip("-") or "unknown"


def lead_key(shop_id: str | None, zip_code: str, trade: str) -> str:
    """Stable id for a lead. Same inputs, same id, every run."""
    return f"{_slug(shop_id or 'unassigned')}:{_slug(zip_code)}:{_slug(trade)}"


def is_weather_event(event: SignalEvent) -> bool:
    return event.source.strip().lower() in WEATHER_SOURCES


def is_strong_signal(event: SignalEvent) -> bool:
    return event.source.strip().lower() in STRONG_SOURCES


def extract_features(
    events: Iterable[SignalEvent],
    *,
    now: datetime | None = None,
    service_area_zips: Iterable[str] | None = None,
    known_customers: Iterable[str] = (),
    recent_lead_keys: Iterable[str] = (),
) -> "OrderedDict[str, LeadFeatures]":
    """Group events into leads and describe each one.

    ``service_area_zips`` of ``None`` means "no service area configured", which
    counts as in-area. An empty set means the opposite — nothing is in area.
    That distinction matters: an unconfigured service area should not silently
    drop every lead.
    """
    moment = _as_utc(now or datetime.now(timezone.utc))
    window_start = moment - timedelta(hours=SIGNAL_WINDOW_HOURS)
    in_area: set[str] | None = (
        None if service_area_zips is None else {z.strip() for z in service_area_zips}
    )
    customers = set(known_customers)
    recent = set(recent_lead_keys)

    grouped: OrderedDict[str, list[SignalEvent]] = OrderedDict()
    for event in sorted(events, key=lambda e: _as_utc(e.observed_at)):
        grouped.setdefault(
            lead_key(event.shop_id, event.zip, event.trade), []
        ).append(event)

    features: OrderedDict[str, LeadFeatures] = OrderedDict()
    for key, group in grouped.items():
        recent_events = [e for e in group if _as_utc(e.observed_at) >= window_start]
        demand = [e for e in recent_events if not is_weather_event(e)]
        head = group[-1]

        features[key] = LeadFeatures(
            signal_count_48h=len(demand),
            weather_trigger=any(is_weather_event(e) for e in recent_events),
            in_service_area=in_area is None or head.zip.strip() in in_area,
            already_customer=key in customers,
            duplicate_30d=key in recent,
            trade=head.trade,
            zip=head.zip,
            shop_id=head.shop_id,
            strong_signal_count=sum(1 for e in demand if is_strong_signal(e)),
            sources=[e.source for e in demand],
        )
    return features


def events_from_payloads(
    payloads: Sequence[Mapping[str, object]], *, shop_id: str | None = None
) -> list[SignalEvent]:
    """Validate raw dicts into events, filling in a batch-level ``shop_id``."""
    events = [SignalEvent.model_validate(payload) for payload in payloads]
    if shop_id:
        for event in events:
            if not event.shop_id:
                event.shop_id = shop_id
    return events
