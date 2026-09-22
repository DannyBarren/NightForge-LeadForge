"""Scoring contracts.

Separate from ``leadforge.models`` on purpose. ``PitchOutput`` is the research
export row and is not forked here; a scored lead is a different thing with a
different lifecycle, and merging them would drag ``EXPORT_COLUMNS`` into a
surface it has nothing to do with.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Band(str, Enum):
    """How hard a lead is pushing."""

    HOT = "hot"
    WARM = "warm"
    LOG = "log"


class RouteDecision(str, Enum):
    """What happens next.

    Nothing in ``heuristic`` emits ``ROUTE`` today. Every qualifying lead lands
    on ``REVIEW`` because a person decides. The value exists so that a future
    opt-in auto-route has somewhere to go, not because anything auto-routes.
    """

    ROUTE = "route"
    REVIEW = "review"
    DROP = "drop"


class SignalEvent(BaseModel):
    """One observed public demand signal.

    ``url`` points at the public page the signal came from. Nothing here is
    scraped from behind a login.
    """

    source: str
    url: str
    text: str
    zip: str
    trade: str
    observed_at: datetime
    shop_id: Optional[str] = None


class LeadFeatures(BaseModel):
    """What the rules get to look at.

    ``strong_signal_count`` and ``sources`` are carried alongside the counts so
    the heuristic can apply the "one strong signal plus a weather trigger" rule
    and can name the signals it used. Without them the reasons list would be a
    number with no provenance, which is the black box this package exists to
    avoid.
    """

    signal_count_48h: int = 0
    weather_trigger: bool = False
    in_service_area: bool = True
    already_customer: bool = False
    duplicate_30d: bool = False
    trade: str = ""
    zip: str = ""
    shop_id: Optional[str] = None
    strong_signal_count: int = 0
    sources: list[str] = Field(default_factory=list)


class ScoredLead(BaseModel):
    """A lead with its band, its score, and the reasons for both."""

    lead_id: str
    shop_id: Optional[str] = None
    features: LeadFeatures
    score: float = Field(ge=0.0, le=1.0)
    band: Band
    confidence_lo: float = Field(ge=0.0, le=1.0)
    confidence_hi: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    suggested_zone: Optional[str] = None
    suggested_unit: Optional[str] = None
    route_decision: RouteDecision
    human_review_required: bool = True

    @field_validator("human_review_required", mode="before")
    @classmethod
    def always_require_human_review(cls, _value: object) -> bool:
        """Pinned on. A caller that passes ``False`` still gets ``True``."""
        return True


class ClosedOutcome(str, Enum):
    """How a pursued lead finished."""

    WON = "won"
    LOST = "lost"


class DecisionLog(BaseModel):
    """What happened to a lead. Written by people and by job status, not by rules.

    Two moments land in the same record type. The first is the decision — a
    human accepted or rejected the lead. The second arrives later, when the job
    it became is won or lost. Keeping them in one shape means the training set
    is one file rather than a join.

    ``closed`` only makes sense on a pursued lead; ``outcomes.record_job_result``
    enforces that rather than the model, so a contradictory webhook payload is
    normalised instead of returning a 500.
    """

    lead_id: str
    shop_id: Optional[str] = None
    pursued: bool
    closed: Optional[ClosedOutcome] = None
    revenue: Optional[float] = None
    unit: Optional[str] = None
    decided_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
