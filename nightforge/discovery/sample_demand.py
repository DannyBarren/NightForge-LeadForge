"""DEMO SEED homeowners, so a sample run never dead-ends on a dead API.

Three fictional households, shaped to land in all three scoring bands:

1. HVAC in 85001 — three public signals inside 48 hours, so ``hot``.
2. Plumbing in 78701 — one strong signal plus a weather flag, so ``warm``.
3. Electrical in 80202 — one weak passing mention, so ``log``.

Every name is obviously fake, every phone number is a 555 number, and every URL
points at ``example.com``. No real person or property is described.

These are the same three people as ``nightforge.scoring.sample_events``, by
design: ``research --sample`` and ``score --sample`` should show one household
set, not two that happen to look similar. The signal events are delegated to
that module rather than copied, so there is exactly one definition of what the
three demo households publicly said. A test asserts the two views agree on the
trade and zip of each.

Weather is a flag carried by a ``weather_alert`` event, never a fourth demand
source. See ``nightforge.discovery.weather``.
"""

from __future__ import annotations

from datetime import datetime

from leadforge.models import DiscoveredLead
from nightforge.discovery.weather import weather_trigger_for
from nightforge.scoring.sample_events import (
    SAMPLE_SERVICE_AREA_ZIPS,
    SAMPLE_SHOP_ID,
    SAMPLE_ZONE_MAP,
    sample_signal_events,
)
from nightforge.scoring.schema import SignalEvent

__all__ = [
    "SAMPLE_SERVICE_AREA_ZIPS",
    "SAMPLE_SHOP_ID",
    "SAMPLE_ZONE_MAP",
    "sample_demand_events",
    "sample_demand_leads",
]

# Household label, trade, city/state, zip, published 555 number, public
# signals, and the public URLs they came from. ``website`` stays null on every
# one: a homeowner is not a business, and there is no site to fetch.
_DEMO_HOUSEHOLDS: tuple[dict, ...] = (
    {
        "company": "Household — Pat Placeholder (85001)",
        "industry": "HVAC",
        "location": "Phoenix, AZ 85001",
        "phone": "(602) 555-0142",
        "pain_signals": [
            "Public board post: AC quit overnight, upstairs unusable",
            "Public Q&A follow-up: compressor still not kicking on, asking for a quote",
            "Public directory note: household listed as seeking same-week HVAC service",
        ],
        "source_urls": [
            "https://example.com/demo-board/ac-out-85001",
            "https://example.com/demo-qa/ac-repair-phoenix",
            "https://example.com/demo-directory/85001-hvac",
        ],
        "discovery_notes": (
            "DEMO SEED — fictional homeowner. Three separate public signals "
            "inside 48 hours all describe the same failed air conditioner, and "
            "the most recent one explicitly asks for a quote. Sourcing is "
            "strong: two of the three are the homeowner posting in their own "
            "words. Human review required."
        ),
    },
    {
        "company": "Household — Jordan Notreal (78701)",
        "industry": "plumbing",
        "location": "Austin, TX 78701",
        "phone": "(512) 555-0198",
        "pain_signals": [
            "Public board post: water heater leaking into the garage, asking for a plumber",
            "Area under a hard freeze advisory (weather flag, not a demand signal)",
        ],
        "source_urls": [
            "https://example.com/demo-board/water-heater-78701",
            "https://example.com/demo-weather/78701-freeze",
        ],
        "discovery_notes": (
            "DEMO SEED — fictional homeowner. One strong public request in the "
            "homeowner's own words, plus a freeze advisory for the area that "
            "makes a burst or failing water heater more plausible. The weather "
            "supports the request; it is not a second request. Human review "
            "required."
        ),
    },
    {
        "company": "Household — Alex Fictitious (80202)",
        "industry": "electrical",
        "location": "Denver, CO 80202",
        "phone": "(720) 555-0176",
        "pain_signals": [
            "Public directory note: breaker panel trips occasionally, no urgency stated",
        ],
        "source_urls": [
            "https://example.com/demo-directory/80202-electrical",
        ],
        "discovery_notes": (
            "DEMO SEED — fictional homeowner. A single passing public mention "
            "with no urgency and no direct request. Logged for context rather "
            "than worked. Human review required."
        ),
    },
)


def sample_demand_leads() -> list[DiscoveredLead]:
    """The three demo households as ``DiscoveredLead`` rows.

    Reuses the shared contract rather than forking it. ``company`` carries the
    household label and ``pain_signals`` the public request, which is how the
    export contract maps a homeowner onto business-shaped columns.
    """
    return [
        DiscoveredLead(
            company=household["company"],
            industry=household["industry"],
            location=household["location"],
            lead_scope="local",
            website=None,
            email=None,
            phone=household["phone"],
            linkedin_url=None,
            pain_signals=list(household["pain_signals"]),
            source_urls=list(household["source_urls"]),
            discovery_notes=household["discovery_notes"],
        )
        for household in _DEMO_HOUSEHOLDS
    ]


def sample_demand_events(now: datetime | None = None) -> list[SignalEvent]:
    """The same three households as scoring signals.

    Delegated so the demand seeds and the scoring seeds cannot drift apart.
    """
    return sample_signal_events(now=now)


def sample_weather_flags() -> dict[str, bool]:
    """What the weather fixture says about each demo zip."""
    return {
        household["location"].rsplit(" ", 1)[-1]: weather_trigger_for(
            household["location"].rsplit(" ", 1)[-1]
        )
        for household in _DEMO_HOUSEHOLDS
    }
