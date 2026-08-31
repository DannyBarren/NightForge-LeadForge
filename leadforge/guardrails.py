"""Safety guardrails: lead caps, token limits, parallel research, budget stops."""

from __future__ import annotations

import logging
import os

from leadforge.config_loader import AppConfig
from leadforge.cost_tracker import CostTracker
from leadforge.models import DiscoveredLead

logger = logging.getLogger(__name__)

# Conservative per-lead estimates for pre-flight budget checks
EST_DISCOVERY_IN, EST_DISCOVERY_OUT = 3_000, 4_000
EST_RESEARCH_IN, EST_RESEARCH_OUT = 2_500, 2_000
EST_PITCH_IN, EST_PITCH_OUT = 1_800, 1_200


class GuardrailViolation(Exception):
    """Raised when a hard limit is exceeded."""


class Guardrails:
    def __init__(self, config: AppConfig, cost: CostTracker):
        self.config = config
        self.cost = cost
        self._stopped = False
        self._stop_reason: str | None = None

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    def cap_leads(self, leads: list[DiscoveredLead]) -> list[DiscoveredLead]:
        cap = self.config.guardrails.max_leads_processed
        if len(leads) > cap:
            logger.warning("Capping leads from %s to %s", len(leads), cap)
            return leads[:cap]
        return leads

    def check_budget_before_phase(
        self, phase: str, estimated_in: int, estimated_out: int
    ) -> None:
        ok, reason = self.cost.can_afford(estimated_in, estimated_out)
        if not ok:
            self._stopped = True
            self._stop_reason = f"{phase}: {reason}"
            logger.warning("Budget guardrail: %s", self._stop_reason)
            raise GuardrailViolation(self._stop_reason)

    def check_remaining_pipeline(
        self,
        phase: str,
        *,
        leads_left_research: int = 0,
        leads_left_pitch: int = 0,
    ) -> None:
        """Project cost for remaining research + pitch work."""
        est_in = leads_left_research * EST_RESEARCH_IN + leads_left_pitch * EST_PITCH_IN
        est_out = (
            leads_left_research * EST_RESEARCH_OUT + leads_left_pitch * EST_PITCH_OUT
        )
        self.check_budget_before_phase(phase, est_in, est_out)

    def mark_stopped(self, reason: str) -> None:
        self._stopped = True
        self._stop_reason = reason

    def sync_stop_from_cost(self) -> bool:
        """If cost tracker tripped, mirror state. Returns True if stopped."""
        stop, reason = self.cost.should_stop()
        if stop:
            self.mark_stopped(reason)
        return stop

    def per_agent_max_tokens(self) -> int:
        return self.config.guardrails.per_agent_max_output_tokens

    def max_parallel_research(self) -> int:
        raw = os.getenv("LEADFORGE_PARALLEL_RESEARCH")
        if raw:
            try:
                n = int(raw)
                return max(1, min(8, n))
            except ValueError:
                pass
        return max(1, min(8, self.config.guardrails.max_parallel_research))

    def human_review_required(self) -> bool:
        return self.config.guardrails.human_review_required
