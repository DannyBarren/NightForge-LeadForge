"""Suggest a zone and a unit. Suggest — nothing here books anything.

Untrained and intentionally dull: a zip-to-zone lookup with a prefix fallback,
and the first roster entry whose trade matches. No LightGBM, no optimiser, no
travel-time model.

What this module must never do, and what a test enforces: write to a calendar,
call an adapter write method, or contact anybody. It returns two strings and a
human decides what to do with them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from nightforge.scoring.schema import LeadFeatures

ZIP_PREFIX_LENGTH = 3


@dataclass(frozen=True)
class Unit:
    """A crew that could take the work. Fixture data, not a real roster."""

    unit_id: str
    trade: str
    zone: str | None = None


# A tiny in-memory roster so the spine runs end to end. Obviously fake.
DEFAULT_ROSTER: tuple[Unit, ...] = (
    Unit("DEMO-UNIT-A", "HVAC"),
    Unit("DEMO-UNIT-B", "plumbing"),
    Unit("DEMO-UNIT-C", "electrical"),
)


def suggest_zone(
    zip_code: str, zone_map: Mapping[str, str] | None = None
) -> str | None:
    """Explicit map first, zip prefix second.

    The prefix fallback keeps a zip we have never seen from falling out of the
    pipeline entirely — an unmapped lead still lands somewhere a dispatcher can
    see, rather than silently losing its zone.
    """
    cleaned = (zip_code or "").strip()
    if not cleaned:
        return None
    if zone_map and cleaned in zone_map:
        return zone_map[cleaned]
    if len(cleaned) >= ZIP_PREFIX_LENGTH:
        return f"zone-{cleaned[:ZIP_PREFIX_LENGTH]}"
    return None


def suggest_unit(trade: str, roster: Sequence[Unit] | None = None) -> str | None:
    """First roster entry whose trade matches. No balancing, no availability."""
    wanted = (trade or "").strip().lower()
    if not wanted:
        return None
    for unit in roster if roster is not None else DEFAULT_ROSTER:
        if unit.trade.strip().lower() == wanted:
            return unit.unit_id
    return None


def suggest(
    features: LeadFeatures,
    *,
    zone_map: Mapping[str, str] | None = None,
    roster: Sequence[Unit] | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(suggested_zone, suggested_unit)`` for one lead."""
    return (
        suggest_zone(features.zip, zone_map),
        suggest_unit(features.trade, roster),
    )
