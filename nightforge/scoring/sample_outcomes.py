"""Checked-in demo labels, so the training slot has something to count.

These label the three fictional households in ``sample_events`` — the ones with
``example.com`` URLs and 555 phone numbers. No real person, business, or job is
described here, and no revenue figure reflects anything real.

This is the only outcome data in the repository. Live outcomes are written to
``data/outcomes/`` and gitignored.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nightforge.scoring.features import lead_key
from nightforge.scoring.sample_events import SAMPLE_SHOP_ID
from nightforge.scoring.schema import ClosedOutcome, DecisionLog

# The same three demo leads sample_events produces.
DEMO_HVAC_LEAD = lead_key(SAMPLE_SHOP_ID, "85001", "HVAC")
DEMO_PLUMBING_LEAD = lead_key(SAMPLE_SHOP_ID, "78701", "plumbing")
DEMO_ELECTRICAL_LEAD = lead_key(SAMPLE_SHOP_ID, "80202", "electrical")


def sample_outcome_rows(now: datetime | None = None) -> list[DecisionLog]:
    """Three demo labels: one won, one lost, one never pursued.

    Deliberately far below the fit threshold. Three rows train nothing, which
    is the point — the slot should skip, visibly, rather than fit noise.
    """
    moment = now or datetime.now(timezone.utc)

    def ago(days: float) -> datetime:
        return moment - timedelta(days=days)

    return [
        DecisionLog(
            lead_id=DEMO_HVAC_LEAD,
            shop_id=SAMPLE_SHOP_ID,
            pursued=True,
            closed=ClosedOutcome.WON,
            revenue=1234.00,
            unit="DEMO-UNIT-A",
            decided_at=ago(12),
        ),
        DecisionLog(
            lead_id=DEMO_PLUMBING_LEAD,
            shop_id=SAMPLE_SHOP_ID,
            pursued=True,
            closed=ClosedOutcome.LOST,
            revenue=None,
            unit="DEMO-UNIT-B",
            decided_at=ago(9),
        ),
        DecisionLog(
            lead_id=DEMO_ELECTRICAL_LEAD,
            shop_id=SAMPLE_SHOP_ID,
            pursued=False,
            closed=None,
            revenue=None,
            unit=None,
            decided_at=ago(5),
        ),
    ]
