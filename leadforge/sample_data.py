"""Built-in seed leads so sample/demo runs always produce output.

These are illustrative, public-style trades businesses used only when live
discovery returns too few leads during a sample run. They keep a live demo
reliable even when search or the LLM is rate-limited or unavailable. They are
clearly marked as demo seeds and always require human review before any use.
"""

from __future__ import annotations

from leadforge.models import DiscoveredLead

_SAMPLE_SEEDS: list[dict] = [
    {
        "company": "Summit Ridge Heating & Air",
        "industry": "HVAC",
        "location": "Phoenix, AZ",
        "lead_scope": "local",
        "website": "https://example.com/summit-ridge-hvac",
        "email": None,
        "phone": "(602) 555-0143",
        "linkedin_url": None,
        "pain_signals": [
            "Google reviews mention missed calls during summer peak",
            "No online booking on website; contact is a form only",
        ],
        "source_urls": ["https://example.com/summit-ridge-hvac"],
        "discovery_notes": (
            "Demo seed lead. Owner-operated HVAC shop showing peak-season "
            "scheduling strain and no online booking. Human review required."
        ),
    },
    {
        "company": "BlueLine Plumbing Co.",
        "industry": "plumbing",
        "location": "Austin, TX",
        "lead_scope": "local",
        "website": "https://example.com/blueline-plumbing",
        "email": "office@example.com",
        "phone": "(512) 555-0198",
        "linkedin_url": None,
        "pain_signals": [
            "Recent hiring post for an office dispatcher/admin",
            "Reviews cite slow callbacks on estimate requests",
        ],
        "source_urls": ["https://example.com/blueline-plumbing"],
        "discovery_notes": (
            "Demo seed lead. Plumbing company hiring admin help and showing "
            "estimate follow-up delays. Human review required."
        ),
    },
    {
        "company": "Copperfield Electric LLC",
        "industry": "electrical",
        "location": "Denver, CO",
        "lead_scope": "remote",
        "website": "https://example.com/copperfield-electric",
        "email": None,
        "phone": "(720) 555-0176",
        "linkedin_url": None,
        "pain_signals": [
            "Outdated website with no scheduling or intake automation",
            "Owner handles invoicing manually per public interview",
        ],
        "source_urls": ["https://example.com/copperfield-electric"],
        "discovery_notes": (
            "Demo seed lead. Electrical contractor with manual invoicing and "
            "an outdated site, strong fit for back-office automation. "
            "Human review required."
        ),
    },
]


def sample_seed_leads(count: int | None = None) -> list[DiscoveredLead]:
    """Return built-in demo seed leads (up to ``count``)."""
    leads = [DiscoveredLead.model_validate(item) for item in _SAMPLE_SEEDS]
    if count is not None:
        return leads[: max(0, count)]
    return leads
