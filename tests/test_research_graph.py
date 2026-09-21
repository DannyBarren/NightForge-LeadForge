"""Tests for the LangGraph research pipeline, run in sample mode with no keys.

Everything here is offline. Sample mode without a provider key skips the live
attempt entirely, so these tests exercise the seed top-up and the deterministic
fallbacks rather than any network path.
"""

import csv
import json
from pathlib import Path

import pytest

from leadforge.config_loader import get_config
from leadforge.export import EXPORT_COLUMNS
from leadforge.models import LeadResearch
from leadforge.run_context import RunOptions
from nightforge.governor import BudgetPolicy, Governor, GovernorHalt, RunKind
from nightforge.graphs import research as rg

PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_API_KEY",
)


@pytest.fixture
def no_keys(monkeypatch):
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def config():
    return get_config()


def _rows(out_dir: Path) -> list[dict]:
    paths = list(out_dir.glob("leads_*.csv"))
    assert len(paths) == 1, f"expected one CSV, found {paths}"
    with paths[0].open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# ---- graph shape --------------------------------------------------------


def test_graph_has_a_governor_node_before_every_spending_phase():
    nodes = set(rg.build_research_graph().nodes)
    assert {
        "preflight",
        "governor_discovery",
        "discover",
        "governor_research",
        "research",
        "governor_pitch",
        "pitch",
        "halt",
        "export",
    } <= nodes


def test_graph_compiles():
    assert rg.compile_research_graph() is not None


# ---- sample mode end to end --------------------------------------------


def test_sample_run_produces_three_complete_rows(tmp_path, no_keys):
    summary = rg.run_research(RunOptions(sample_mode=True), output_directory=tmp_path)

    assert summary["mode"] == "sample"
    assert summary["leads_discovered"] == 3
    assert summary["leads_researched"] == 3
    assert summary["leads_pitched"] == 3
    assert summary["stopped_early"] is False

    rows = _rows(tmp_path)
    assert len(rows) == 3
    for row in rows:
        assert row["Company"].strip()
        assert row["Draft Email"].strip()
        assert row["Barren Fit"].strip()
        assert row["Specific Value Props"].strip()
        assert row["Recommended Next Step"].strip()


def test_export_columns_match_the_prototype_contract(tmp_path, no_keys):
    rg.run_research(RunOptions(sample_mode=True), output_directory=tmp_path)
    paths = list(tmp_path.glob("leads_*.csv"))
    with paths[0].open(encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header == EXPORT_COLUMNS


def test_every_exported_row_requires_human_review(tmp_path, no_keys):
    rg.run_research(RunOptions(sample_mode=True), output_directory=tmp_path)
    assert {row["Human Review"] for row in _rows(tmp_path)} == {"YES"}


def test_manifest_and_token_log_are_written(tmp_path, no_keys):
    summary = rg.run_research(RunOptions(sample_mode=True), output_directory=tmp_path)

    manifest = json.loads(
        next(tmp_path.glob("manifest_*.json")).read_text(encoding="utf-8")
    )
    assert manifest["human_review_required"] is True
    assert manifest["leads_pitched"] == 3
    assert manifest["stopped_early"] is False

    token_log = json.loads(Path(summary["token_log"]).read_text(encoding="utf-8"))
    assert token_log["run_id"] == summary["run_id"]


def test_a_keyless_sample_run_spends_nothing(tmp_path, no_keys, config):
    """No provider key means no live attempt, so nothing is called or billed."""
    governor = Governor.for_research(config)
    rg.run_research(
        RunOptions(sample_mode=True),
        config=config,
        governor=governor,
        output_directory=tmp_path,
    )

    assert governor.total_cost_usd() == 0.0
    assert governor.records == []
    assert governor.loop_state()["tool_calls"] == 0


def test_sample_mode_survives_a_failing_search_and_llm(tmp_path, monkeypatch):
    """The live path is attempted and fails at every step. Still three rows."""
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-never-called")

    def boom(*args, **kwargs):
        raise RuntimeError("search and LLM are both down")

    monkeypatch.setattr(rg, "_search", boom)
    monkeypatch.setattr(rg, "_invoke_llm", boom)

    summary = rg.run_research(RunOptions(sample_mode=True), output_directory=tmp_path)

    assert summary["leads_pitched"] == 3
    assert summary["stopped_early"] is False
    assert {row["Human Review"] for row in _rows(tmp_path)} == {"YES"}


# ---- halting ------------------------------------------------------------


def test_a_governor_halt_still_reaches_export(tmp_path, no_keys, config):
    """An exhausted budget stops the run at the first gate, cleanly."""
    governor = Governor(
        config, policy=BudgetPolicy(RunKind.RESEARCH, 0.01, 0.01)
    )
    governor.record("setup", "seed", 500_000, 500_000)

    summary = rg.run_research(
        RunOptions(sample_mode=True),
        config=config,
        governor=governor,
        output_directory=tmp_path,
    )

    assert summary["stopped_early"] is True
    assert summary["stop_reason"]
    assert summary["leads_pitched"] == 0
    assert summary["export_paths"] == {}
    # The run still ran the export node rather than crashing out of the graph.
    assert Path(summary["token_log"]).is_file()


def test_partial_work_still_exports_when_the_run_halts_mid_pitch(
    tmp_path, no_keys, monkeypatch
):
    real_pitch = rg._pitch_one
    seen = {"count": 0}

    def halting_pitch(config, governor, research, *, live):
        seen["count"] += 1
        if seen["count"] > 2:
            raise GovernorHalt("pitch: synthetic budget stop")
        return real_pitch(config, governor, research, live=live)

    monkeypatch.setattr(rg, "_pitch_one", halting_pitch)

    summary = rg.run_research(RunOptions(sample_mode=True), output_directory=tmp_path)

    assert summary["stopped_early"] is True
    assert "synthetic budget stop" in summary["stop_reason"]
    assert summary["leads_pitched"] == 2

    rows = _rows(tmp_path)
    assert len(rows) == 2
    assert {row["Human Review"] for row in rows} == {"YES"}


# ---- the human-review invariant ----------------------------------------


def test_human_review_is_forced_on_after_model_validation(monkeypatch, config):
    """Model output that tries to turn review off does not get to."""
    monkeypatch.setattr(
        rg,
        "_invoke_llm",
        lambda *args, **kwargs: json.dumps(
            {
                "company": "Acme HVAC",
                "draft_email": "Hi there",
                "human_review_required": False,
                "confidence": "high",
            }
        ),
    )

    pitch = rg._pitch_one(
        config,
        Governor.for_research(config),
        LeadResearch(company="Acme HVAC"),
        live=True,
    )

    assert pitch.human_review_required is True
    assert pitch.to_export_row()["Human Review"] == "YES"


# ---- reuse of the prototype's parts -------------------------------------


def test_research_fallback_carries_discovery_fields_forward():
    from leadforge.sample_data import sample_seed_leads

    lead = sample_seed_leads(1)[0]
    fallback = rg._research_fallback(lead)

    assert fallback.company == lead.company
    assert fallback.pains == lead.pain_signals
    assert fallback.source_urls == lead.source_urls


def test_loop_limits_scale_with_the_lead_count():
    small = rg.default_loop_limits(3)
    large = rg.default_loop_limits(30)
    assert small.max_iterations < large.max_iterations
    # A 30-lead run needs one discovery call plus two per lead.
    assert large.max_iterations >= 1 + 2 * 30


# ---- CLI ----------------------------------------------------------------


def test_cli_sample_run_writes_a_csv(tmp_path, no_keys, capsys):
    from nightforge.cli import main

    exit_code = main(
        ["research", "--sample", "--output-dir", str(tmp_path), "--quiet"]
    )

    assert exit_code == 0
    assert len(_rows(tmp_path)) == 3
    assert "human review required on every row" in capsys.readouterr().out
