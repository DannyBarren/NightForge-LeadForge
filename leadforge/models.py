"""Pydantic models for leads, research, and final pitch output."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DiscoveredLead(BaseModel):
    """Raw lead from discovery phase."""

    company: str
    industry: str
    location: str
    lead_scope: str = "local"
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    pain_signals: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    discovery_notes: str = ""


class LeadResearch(BaseModel):
    """Enriched public research for one lead."""

    company: str
    industry: str = ""
    location: str = ""
    lead_scope: str = ""
    owner_name: Optional[str] = None
    owner_signals: list[str] = Field(default_factory=list)
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    contact_notes: str = ""
    website_summary: str = ""
    review_summary: str = ""
    recent_news: list[str] = Field(default_factory=list)
    pains: list[str] = Field(default_factory=list)
    public_facts: list[str] = Field(default_factory=list)
    barren_fit_analysis: str = ""
    source_urls: list[str] = Field(default_factory=list)


class PitchOutput(BaseModel):
    """Final row for export — matches sample output format."""

    company: str
    owner: str = "Unknown"
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    industry: str = ""
    location: str = ""
    lead_scope: str = ""
    pains: str = ""
    barren_fit: str = ""
    draft_email: str = ""
    pitch_angle: str = ""
    specific_value_props: list[str] = Field(default_factory=list)
    recommended_next_step: str = ""
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    human_review_required: bool = True
    research_sources: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, v: object) -> ConfidenceLevel | object:
        if isinstance(v, str):
            return ConfidenceLevel(v.lower().strip())
        return v

    def to_export_row(self) -> dict[str, str]:
        return {
            "Company": self.company,
            "Owner": self.owner,
            "Email": self.email or "",
            "Phone": self.phone or "",
            "LinkedIn": self.linkedin_url or "",
            "Industry": self.industry,
            "Location": self.location,
            "Lead Scope": self.lead_scope,
            "Pains": self.pains,
            "Barren Fit": self.barren_fit,
            "Draft Email": self.draft_email,
            "Pitch Angle": self.pitch_angle,
            "Specific Value Props": " | ".join(self.specific_value_props),
            "Recommended Next Step": self.recommended_next_step,
            "Confidence": self.confidence.value,
            "Human Review": "YES" if self.human_review_required else "NO",
            "Sources": " | ".join(self.research_sources[:5]),
        }


class RunManifest(BaseModel):
    """Metadata written alongside each overnight run."""

    run_id: str
    started_at: str
    finished_at: Optional[str] = None
    location: str
    industries: list[str]
    leads_discovered: int = 0
    leads_researched: int = 0
    leads_pitched: int = 0
    estimated_cost_usd: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)
    stopped_early: bool = False
    stop_reason: Optional[str] = None
    human_review_required: bool = True

    @staticmethod
    def new_run_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
