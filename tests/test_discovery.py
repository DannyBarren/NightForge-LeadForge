"""Tests for the demand-side flip: homeowners in, shops out.

Offline throughout. No keys, no network, no LLM.
"""

import csv
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from leadforge.config_loader import get_config
from leadforge.export import EXPORT_COLUMNS
from leadforge.sample_data import sample_seed_leads
from nightforge.discovery import prompts, weather
from nightforge.discovery.sample_demand import (
    SAMPLE_SERVICE_AREA_ZIPS,
    sample_demand_events,
    sample_demand_leads,
)
from nightforge.graphs import research as rg
from nightforge.scoring.features import extract_features, is_weather_event, lead_key
from nightforge.scoring.pipeline import score_events
from nightforge.scoring.schema import Band

REPO = Path(__file__).resolve().parent.parent

PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_API_KEY",
)

# Markers that would mean we are back to selling to shops.
SHOP_MARKERS = ("llc", " inc", "co.", "company", "contractor", "heating & air")


@pytest.fixture
def no_keys(monkeypatch):
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


def _flat(text: str) -> str:
    """Lowercase with whitespace collapsed, so wrapping cannot break a match."""
    return " ".join(text.split()).lower()


def _rows(out_dir: Path) -> list[dict]:
    paths = list(out_dir.glob("leads_*.csv"))
    assert len(paths) == 1, f"expected one CSV, found {paths}"
    with paths[0].open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# ---- the seeds are homeowners -------------------------------------------


def test_sample_seeds_are_three_homeowners():
    leads = sample_demand_leads()

    assert len(leads) == 3
    assert [lead.industry for lead in leads] == ["HVAC", "plumbing", "electrical"]
    assert [lead.location.rsplit(" ", 1)[-1] for lead in leads] == [
        "85001",
        "78701",
        "80202",
    ]


def test_sample_seeds_contain_zero_shop_directory_rows():
    """No business names, and none of the prototype's shop seeds."""
    leads = sample_demand_leads()
    shop_names = {seed.company.lower() for seed in sample_seed_leads()}

    for lead in leads:
        label = lead.company.lower()
        assert label.startswith("household")
        assert label not in shop_names
        assert not [marker for marker in SHOP_MARKERS if marker in label]
        # A homeowner is not a business: nothing to fetch, no profile to build.
        assert lead.website is None
        assert lead.linkedin_url is None


def test_sample_seeds_are_labelled_demo_with_555_numbers_and_example_urls():
    for lead in sample_demand_leads():
        assert "DEMO SEED" in lead.discovery_notes
        assert "555-" in (lead.phone or "")
        assert all("example.com" in url for url in lead.source_urls)


def test_the_prototype_shop_seeds_are_still_there_and_still_shops():
    """The flip is additive. leadforge keeps finding shops."""
    shops = sample_seed_leads()
    assert len(shops) == 3
    assert any("LLC" in shop.company or "Co." in shop.company for shop in shops)


# ---- weather is a flag, not a source ------------------------------------


def test_weather_fixture_is_a_hardcoded_map():
    assert weather.weather_trigger_for("78701") is True
    assert weather.weather_trigger_for("85001") is False
    assert weather.triggered_zips() == frozenset({"78701"})


def test_an_unknown_zip_is_not_assumed_to_be_triggered():
    assert weather.weather_trigger_for("99999") is False
    assert weather.weather_trigger_for("") is False


def test_weather_sets_the_flag_without_counting_as_a_demand_signal():
    features = extract_features(sample_demand_events())
    plumbing = features[lead_key("demo-shop-0001", "78701", "plumbing")]

    assert plumbing.weather_trigger is True
    # One demand signal, not two: the advisory is context.
    assert plumbing.signal_count_48h == 1
    assert "weather_alert" not in plumbing.sources


def test_the_weather_fixture_agrees_with_the_demo_signal_set():
    """The fixture map and the demo events must not drift apart."""
    flagged_in_events = {
        event.zip for event in sample_demand_events() if is_weather_event(event)
    }
    assert flagged_in_events == weather.triggered_zips()


def test_a_weather_flag_alone_does_not_make_a_lead():
    """Weather with no public request behind it is not demand."""
    weather_only = [
        event for event in sample_demand_events() if is_weather_event(event)
    ]
    leads = score_events(weather_only, service_area_zips=SAMPLE_SERVICE_AREA_ZIPS)

    assert all(lead.features.signal_count_48h == 0 for lead in leads)
    assert all(lead.band is Band.LOG for lead in leads)


# ---- the research run still works ---------------------------------------


def test_research_sample_still_yields_three_rows_with_human_review(tmp_path, no_keys):
    summary = rg.run_research(
        rg.RunOptions(sample_mode=True), output_directory=tmp_path
    )

    assert summary["leads_pitched"] == 3
    rows = _rows(tmp_path)
    assert len(rows) == 3
    assert {row["Human Review"] for row in rows} == {"YES"}


def test_the_export_contract_is_unchanged(tmp_path, no_keys):
    rg.run_research(rg.RunOptions(sample_mode=True), output_directory=tmp_path)
    paths = list(tmp_path.glob("leads_*.csv"))
    with paths[0].open(encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header == EXPORT_COLUMNS


def test_homeowner_fields_map_onto_the_business_columns(tmp_path, no_keys):
    rg.run_research(rg.RunOptions(sample_mode=True), output_directory=tmp_path)

    for row in _rows(tmp_path):
        assert row["Company"].lower().startswith("household")
        assert row["Owner"] not in ("", "Unknown")
        assert row["Location"][-5:].isdigit()
        assert row["Pains"].strip()


def test_the_draft_is_addressed_to_the_homeowner_not_a_shop(tmp_path, no_keys):
    rg.run_research(rg.RunOptions(sample_mode=True), output_directory=tmp_path)

    for row in _rows(tmp_path):
        draft = row["Draft Email"]
        assert row["Owner"] in draft
        assert "local" in draft.lower()
        # The prototype's software pitch must not leak into a homeowner reply.
        assert "back-office" not in draft.lower()
        assert "barren" not in draft.lower()


def test_a_keyless_sample_run_still_spends_nothing(tmp_path, no_keys):
    config = get_config()
    governor = rg.Governor.for_research(config)

    rg.run_research(
        rg.RunOptions(sample_mode=True),
        config=config,
        governor=governor,
        output_directory=tmp_path,
    )

    assert governor.total_cost_usd() == 0.0
    assert governor.records == []


def test_posting_name_is_read_only_from_a_household_label():
    assert rg.posting_name_from("Household — Pat Placeholder (85001)") == (
        "Pat Placeholder"
    )
    assert rg.posting_name_from("Summit Ridge Heating & Air") is None
    assert rg.posting_name_from("") is None


# ---- scoring shares the same three people -------------------------------


def test_scoring_ingest_of_those_events_bands_hot_warm_log(tmp_path, no_keys):
    summary = rg.run_research(
        rg.RunOptions(sample_mode=True), output_directory=tmp_path
    )

    assert summary["signal_bands"] == {"hot": 1, "warm": 1, "log": 1}
    assert Path(summary["scored_artifact"]).is_file()


def test_the_demand_seeds_and_the_signal_events_describe_the_same_households():
    from_leads = {
        (lead.location.rsplit(" ", 1)[-1], lead.industry.lower())
        for lead in sample_demand_leads()
    }
    from_events = {
        (event.zip, event.trade.lower())
        for event in sample_demand_events()
        if not is_weather_event(event)
    }
    assert from_leads == from_events


def test_a_graph_signal_node_runs_after_export():
    nodes = set(rg.build_research_graph().nodes)
    assert "emit_signals" in nodes
    assert {"export", "pitch", "halt"} <= nodes


def test_production_leads_become_weak_discovery_signals():
    """Our own crawl is weaker evidence than the homeowner's own words."""
    events = rg._signal_events_from_leads(
        sample_demand_leads(), get_config(), now=datetime.now(timezone.utc)
    )

    demand = [event for event in events if not is_weather_event(event)]
    assert demand
    assert {event.source for event in demand} == {rg.DISCOVERY_SIGNAL_SOURCE}

    features = extract_features(events)
    assert all(item.strong_signal_count == 0 for item in features.values())


def test_a_weather_flagged_zip_adds_a_flag_event_in_production():
    events = rg._signal_events_from_leads(sample_demand_leads(), get_config())
    flagged = {event.zip for event in events if is_weather_event(event)}
    assert flagged == weather.triggered_zips()


def test_signal_emission_skips_a_lead_with_no_usable_zip():
    lead = sample_demand_leads()[0].model_copy(update={"location": "Somewhere"})
    config = get_config().model_copy(update={"icp": {}})
    assert rg._signal_events_from_leads([lead], config) == []


# ---- the prompts are demand-side ----------------------------------------


def test_the_discovery_prompt_asks_for_homeowners_and_refuses_shops():
    text = _flat(prompts.demand_discovery_prompt(get_config()))

    assert "homeowners or property owners" in text
    assert "you are looking for demand, not suppliers" in text
    for forbidden in ("directories", "login", "crm record"):
        assert forbidden in text


def test_the_discovery_prompt_says_weather_is_not_demand():
    assert "weather is context, not demand" in _flat(
        prompts.demand_discovery_prompt(get_config())
    )


def test_the_research_prompt_refuses_to_profile_a_private_individual():
    text = _flat(prompts.demand_research_prompt(sample_demand_leads()[0]))

    assert "private individual, not a business" in text
    assert "do not" in text
    assert "build a personal profile" in text


def test_the_pitch_prompt_writes_from_the_shop_to_the_homeowner():
    text = _flat(prompts.demand_pitch_prompt("{}"))

    assert "as the shop, to the homeowner" in text
    assert "nothing is sent" in text
    assert "not about software" in text


def test_the_graph_no_longer_uses_the_shop_prompts():
    source = (REPO / "nightforge" / "graphs" / "research.py").read_text(
        encoding="utf-8"
    )
    for shop_prompt in ("discovery_task", "research_task(", "pitch_task("):
        assert shop_prompt not in source


# ---- the prototype is untouched -----------------------------------------


@pytest.mark.parametrize(
    "path", ["leadforge/tasks.py", "leadforge/sample_data.py"]
)
def test_the_prototype_files_have_empty_diffs(path):
    result = subprocess.run(
        ["git", "diff", "HEAD", "--", path],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("git is unavailable in this environment")
    assert result.stdout == "", f"{path} has uncommitted changes"


def test_the_shop_side_prompts_still_exist_untouched():
    from leadforge.tasks import discovery_task

    text = discovery_task(None, get_config()).description.lower()
    assert "small businesses" in text
