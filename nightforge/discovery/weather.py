"""Weather triggers — a hardcoded fixture, deliberately.

No HTTP, no NOAA, no OpenWeather, no API key. ``weather_trigger_for`` reads a
map in this file and returns a boolean.

A real integration replaces the body of this one function and nothing else.
Keeping the seam this narrow is the point: the scoring rules already treat
weather as a flag on a signal rather than a source of demand, so the only thing
a live feed would change is where the boolean comes from.
"""

from __future__ import annotations

from typing import Mapping

# Zips currently under a weather event, per the fixture. 78701 matches the
# freeze advisory in the demo signal set, so the demo plumbing lead bands warm.
WEATHER_TRIGGER_ZIPS: Mapping[str, bool] = {
    "78701": True,
    "85001": False,
    "80202": False,
}


def weather_trigger_for(zip_code: str) -> bool:
    """Is this zip under a weather event?

    Unknown zips return ``False``. An unmapped zip means "no evidence of a
    weather event", not "assume one" — inventing a trigger would promote a
    one-signal lead to warm on no evidence at all.
    """
    return bool(WEATHER_TRIGGER_ZIPS.get((zip_code or "").strip(), False))


def triggered_zips() -> frozenset[str]:
    """The zips the fixture currently marks as triggered."""
    return frozenset(
        zip_code for zip_code, flagged in WEATHER_TRIGGER_ZIPS.items() if flagged
    )
