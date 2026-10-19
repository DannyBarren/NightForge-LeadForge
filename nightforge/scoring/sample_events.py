"""Labelled DEMO demand events so the scoring spine runs with no network.

These are homeowners asking for work in public, not shops. Every name is
obviously fake, every phone number is a 555 number, and every URL points at
``example.com``. Nothing here describes a real person or a real business.

The three leads are shaped to exercise all three bands end to end:

- HVAC in 85001 — three agreeing signals inside 48 hours, so ``hot``.
- Plumbing in 78701 — one strong signal plus a weather trigger, so ``warm``.
- Electrical in 80202 — a single passing mention, so ``log``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nightforge.scoring.schema import SignalEvent

SAMPLE_SHOP_ID = "demo-shop-0001"

SAMPLE_SERVICE_AREA_ZIPS = frozenset({"85001", "78701", "80202"})

# The explicit zone map the assigner prefers over its zip-prefix fallback.
SAMPLE_ZONE_MAP = {
    "85001": "demo-zone-north",
    "78701": "demo-zone-central",
    "80202": "demo-zone-downtown",
}


def sample_signal_events(now: datetime | None = None) -> list[SignalEvent]:
    """Five demo events that group into three leads.

    Timestamps are relative to ``now`` so the 48-hour window always applies;
    fixed dates would age out and silently turn every lead into a log entry.
    """
    moment = now or datetime.now(timezone.utc)

    def ago(hours: float) -> datetime:
        return moment - timedelta(hours=hours)

    return [
        # Lead one: three agreeing signals in 48h -> hot.
        SignalEvent(
            source="community_board_post",
            url="https://example.com/demo-board/ac-out-85001",
            text=(
                "DEMO SEED — Pat Placeholder (fictional homeowner): AC quit "
                "overnight, upstairs is unusable. Anyone available this week? "
                "Reach me at (602) 555-0142."
            ),
            zip="85001",
            trade="HVAC",
            observed_at=ago(30),
            shop_id=SAMPLE_SHOP_ID,
        ),
        SignalEvent(
            source="public_qa_thread",
            url="https://example.com/demo-qa/ac-repair-phoenix",
            text=(
                "DEMO SEED — Pat Placeholder (fictional homeowner): follow-up, "
                "the compressor is still not kicking on. Looking for a quote."
            ),
            zip="85001",
            trade="HVAC",
            observed_at=ago(20),
            shop_id=SAMPLE_SHOP_ID,
        ),
        SignalEvent(
            source="public_directory_mention",
            url="https://example.com/demo-directory/85001-hvac",
            text=(
                "DEMO SEED — public directory note: fictional household in "
                "85001 listed as seeking same-week HVAC service."
            ),
            zip="85001",
            trade="HVAC",
            observed_at=ago(6),
            shop_id=SAMPLE_SHOP_ID,
        ),
        # Lead two: one strong signal plus a weather trigger -> warm.
        SignalEvent(
            source="community_board_post",
            url="https://example.com/demo-board/water-heater-78701",
            text=(
                "DEMO SEED — Jordan Notreal (fictional homeowner): water heater "
                "is leaking into the garage. Need a plumber. (512) 555-0198."
            ),
            zip="78701",
            trade="plumbing",
            observed_at=ago(10),
            shop_id=SAMPLE_SHOP_ID,
        ),
        SignalEvent(
            source="weather_alert",
            url="https://example.com/demo-weather/78701-freeze",
            text="DEMO SEED — fictional hard freeze advisory for the 78701 area.",
            zip="78701",
            trade="plumbing",
            observed_at=ago(9),
            shop_id=SAMPLE_SHOP_ID,
        ),
        # Lead three: a single passing mention -> log.
        SignalEvent(
            source="public_directory_mention",
            url="https://example.com/demo-directory/80202-electrical",
            text=(
                "DEMO SEED — Alex Fictitious (fictional homeowner): breaker "
                "panel trips occasionally. No urgency stated. (720) 555-0176."
            ),
            zip="80202",
            trade="electrical",
            observed_at=ago(14),
            shop_id=SAMPLE_SHOP_ID,
        ),
    ]
