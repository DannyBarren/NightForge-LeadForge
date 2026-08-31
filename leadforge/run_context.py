"""Per-run options: sample mode, lead caps, and effective guardrail overrides."""

from __future__ import annotations

from dataclasses import dataclass

from leadforge.config_loader import AppConfig

# Default parallel research workers for sample/demo runs. Capped low so live
# demos do not trip search-provider rate limits.
SAMPLE_PARALLEL_RESEARCH_CAP = 4


@dataclass(frozen=True)
class RunOptions:
    """CLI / UI overrides applied for a single pipeline execution."""

    config_check_only: bool = False
    sample_mode: bool = False
    max_leads: int | None = None
    target_zip: str | None = None
    target_industries: list[str] | None = None
    budget_usd: float | None = None
    stop_projected_usd: float | None = None
    parallel_research: int | None = None
    llm_provider: str | None = None
    llm_model: str | None = None

    def effective_max_leads(self, config: AppConfig) -> int:
        base = config.guardrails.max_leads_processed
        if self.sample_mode:
            return min(3, base if base else 3)
        if self.max_leads is not None:
            return max(1, min(self.max_leads, 40))
        return base

    def effective_parallel_research(self, base: int) -> int:
        if self.parallel_research is not None:
            workers = max(1, min(8, self.parallel_research))
        else:
            workers = max(1, min(8, base))
        if self.sample_mode:
            workers = min(SAMPLE_PARALLEL_RESEARCH_CAP, workers)
        return workers

    def apply_to_config(self, config: AppConfig) -> AppConfig:
        g = config.guardrails.model_copy()
        cap = self.effective_max_leads(config)
        g.max_leads_processed = cap

        if cap <= 5:
            g.discovery_target_min = cap
            g.discovery_target_max = cap

        g.max_parallel_research = self.effective_parallel_research(
            g.max_parallel_research
        )

        if self.budget_usd is not None:
            g.budget_usd = max(0.5, min(self.budget_usd, 10.0))
        if self.stop_projected_usd is not None:
            g.stop_projected_usd = max(0.25, min(self.stop_projected_usd, g.budget_usd))
        elif g.stop_projected_usd > g.budget_usd:
            g.stop_projected_usd = g.budget_usd

        updates: dict = {"guardrails": g}

        llm = config.llm.model_copy()
        llm_changed = False
        if self.llm_provider:
            llm.provider = self.llm_provider
            llm_changed = True
        if self.llm_model:
            llm.model = self.llm_model
            llm_changed = True
        if llm_changed:
            updates["llm"] = llm

        icp = dict(config.icp)
        icp_changed = False
        if self.target_zip:
            zip_code = self.target_zip.strip()
            icp["zip_code"] = zip_code
            icp["geography_override"] = (
                f"ZIP {zip_code} local market (~100 mile radius) "
                "plus remote-ready US leads"
            )
            icp_changed = True
        if self.target_industries:
            icp["industries_override"] = list(self.target_industries)
            icp_changed = True
        if icp_changed:
            updates["icp"] = icp

        return config.model_copy(update=updates)
