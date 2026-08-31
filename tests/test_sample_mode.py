"""Tests for sample-mode reliability: seed leads, fallback pitch, run options."""

from leadforge.config_loader import AppConfig, GuardrailsConfig, LLMConfig
from leadforge.main import _fallback_pitch
from leadforge.models import LeadResearch
from leadforge.run_context import RunOptions
from leadforge.sample_data import sample_seed_leads


def _config() -> AppConfig:
    return AppConfig(
        guardrails=GuardrailsConfig(max_parallel_research=6, max_leads_processed=30),
        llm=LLMConfig(),
    )


def test_sample_seed_leads_are_valid():
    leads = sample_seed_leads()
    assert len(leads) >= 3
    for lead in leads:
        assert lead.company
        assert lead.industry
        assert lead.location


def test_sample_seed_leads_count_limit():
    assert len(sample_seed_leads(2)) == 2


def test_fallback_pitch_is_complete_and_flagged():
    research = LeadResearch(
        company="Acme HVAC",
        industry="HVAC",
        location="Phoenix, AZ",
        pains=["missed calls", "no online booking"],
    )
    pitch = _fallback_pitch(research)
    row = pitch.to_export_row()
    assert row["Human Review"] == "YES"
    assert row["Draft Email"].strip()
    assert "Acme HVAC" in row["Draft Email"]
    assert row["Barren Fit"].strip()
    assert row["Specific Value Props"].strip()


def test_sample_mode_caps_parallel_research_at_four():
    cfg = _config()
    applied = RunOptions(sample_mode=True).apply_to_config(cfg)
    assert applied.guardrails.max_parallel_research == 4
    assert applied.guardrails.max_leads_processed == 3


def test_run_options_apply_ui_fields():
    cfg = _config()
    applied = RunOptions(
        max_leads=10,
        target_zip="85001",
        target_industries=["HVAC", "plumbing"],
        budget_usd=6.0,
        stop_projected_usd=5.0,
        parallel_research=2,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
    ).apply_to_config(cfg)
    assert applied.guardrails.max_leads_processed == 10
    assert applied.guardrails.budget_usd == 6.0
    assert applied.guardrails.stop_projected_usd == 5.0
    assert applied.guardrails.max_parallel_research == 2
    assert applied.icp["zip_code"] == "85001"
    assert applied.icp["industries_override"] == ["HVAC", "plumbing"]


def test_budget_cap_never_exceeds_ten():
    cfg = _config()
    applied = RunOptions(budget_usd=999.0).apply_to_config(cfg)
    assert applied.guardrails.budget_usd == 10.0
