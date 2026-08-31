"""Tests for budget guardrails."""

import pytest

from leadforge.config_loader import AppConfig, GuardrailsConfig, LLMConfig
from leadforge.cost_tracker import CostTracker
from leadforge.guardrails import Guardrails, GuardrailViolation


def _config(budget: float = 7.0, stop: float = 5.0) -> AppConfig:
    return AppConfig(
        guardrails=GuardrailsConfig(budget_usd=budget, stop_projected_usd=stop),
        llm=LLMConfig(),
        pricing_usd_per_million={
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        },
    )


def test_hard_budget_stop():
    cfg = _config(budget=0.001, stop=0.001)
    cost = CostTracker(config=cfg, model="gpt-4o-mini")
    g = Guardrails(cfg, cost)
    cost.record("test", "phase", 500_000, 500_000)
    with pytest.raises(GuardrailViolation):
        g.check_budget_before_phase("next", 1000, 1000)


def test_parallel_research_cap():
    import os

    cfg = _config()
    cost = CostTracker(config=cfg, model="gpt-4o-mini")
    g = Guardrails(cfg, cost)
    os.environ["LEADFORGE_PARALLEL_RESEARCH"] = "12"
    try:
        assert g.max_parallel_research() == 8
    finally:
        os.environ.pop("LEADFORGE_PARALLEL_RESEARCH", None)


def test_cost_summary_includes_budget_details():
    cfg = _config(budget=8.0, stop=7.25)
    cost = CostTracker(config=cfg, model="gpt-4o-mini")
    cost.record("test", "phase", 1000, 1000)
    summary = cost.summary()
    assert summary["budget_usd"] == 8.0
    assert summary["early_stop_usd"] == 7.25
    assert "budget_remaining_usd" in summary
    assert summary["records"][0]["cumulative_usd"] >= summary["records"][0]["estimated_usd"]
