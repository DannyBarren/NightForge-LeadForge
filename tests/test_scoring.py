"""Tests for the scoring spine: features, rules, assignment, and the routes.

Entirely offline. No keys, no LLM, no model, no vendor call.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from nightforge.adapters.jobber import JobberAdapter
from nightforge.api import app as api
from nightforge.scoring import assigner, heuristic
from nightforge.scoring.features import extract_features, lead_key
from nightforge.scoring.pipeline import ingest, rank, run_sample, score_events
from nightforge.scoring.sample_events import (
    SAMPLE_SERVICE_AREA_ZIPS,
    SAMPLE_ZONE_MAP,
    sample_signal_events,
)
from nightforge.scoring.schema import (
    Band,
    LeadFeatures,
    RouteDecision,
    ScoredLead,
    SignalEvent,
)

ADAPTER_WRITE_METHODS = (
    "hydrate",
    "find_or_create_client",
    "create_request_or_job",
    "place_draft",
)


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_output_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def client(out_dir, monkeypatch):
    monkeypatch.setattr(api, "_LEADS", {})
    return TestClient(api.app)


def _features(**overrides) -> LeadFeatures:
    base = {
        "signal_count_48h": 1,
        "trade": "HVAC",
        "zip": "85001",
        "shop_id": "demo-shop-0001",
        "sources": ["community_board_post"],
        "strong_signal_count": 1,
    }
    return LeadFeatures(**{**base, **overrides})


# ---- the sample batch ---------------------------------------------------


def test_sample_ingest_yields_three_scored_leads_offline(tmp_path):
    result = run_sample(output_directory=tmp_path)

    assert len(result.leads) == 3
    assert all(isinstance(lead, ScoredLead) for lead in result.leads)
    assert result.band_counts() == {"hot": 1, "warm": 1, "log": 1}


def test_hot_ranks_above_log(tmp_path):
    leads = run_sample(output_directory=tmp_path).leads
    bands = [lead.band for lead in leads]

    assert bands.index(Band.HOT) < bands.index(Band.LOG)
    assert bands == [Band.HOT, Band.WARM, Band.LOG]


def test_warm_is_not_hidden(tmp_path):
    """The mid band is the one worth a human's time. It stays in the list."""
    leads = run_sample(output_directory=tmp_path).leads
    assert any(lead.band is Band.WARM for lead in leads)


def test_human_review_required_on_every_scored_lead(tmp_path):
    for lead in run_sample(output_directory=tmp_path).leads:
        assert lead.human_review_required is True


def test_human_review_cannot_be_turned_off():
    lead = ScoredLead(
        lead_id="x",
        features=_features(),
        score=0.5,
        band=Band.WARM,
        confidence_lo=0.4,
        confidence_hi=0.6,
        route_decision=RouteDecision.REVIEW,
        human_review_required=False,
    )
    assert lead.human_review_required is True


def test_sample_artifact_is_written_and_readable(tmp_path):
    result = run_sample(output_directory=tmp_path)

    assert result.artifact_path.name.startswith("scored_")
    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["lead_count"] == 3
    assert payload["human_review_required"] is True
    assert len(payload["leads"]) == 3


def test_scores_are_deterministic(tmp_path):
    moment = datetime.now(timezone.utc)
    first = run_sample(output_directory=tmp_path, now=moment)
    second = run_sample(output_directory=tmp_path, now=moment)

    assert [(l.lead_id, l.score, l.band) for l in first.leads] == [
        (l.lead_id, l.score, l.band) for l in second.leads
    ]


# ---- the rules ----------------------------------------------------------


def test_three_agreeing_signals_are_hot_and_go_to_review():
    verdict = heuristic.evaluate(
        _features(signal_count_48h=3, sources=["a", "b", "c"], strong_signal_count=1)
    )
    assert verdict.band is Band.HOT
    assert verdict.route_decision is RouteDecision.REVIEW


def test_two_signals_are_warm():
    verdict = heuristic.evaluate(
        _features(signal_count_48h=2, sources=["a", "b"], strong_signal_count=0)
    )
    assert verdict.band is Band.WARM
    assert verdict.route_decision is RouteDecision.REVIEW


def test_one_strong_signal_with_a_weather_trigger_is_warm():
    verdict = heuristic.evaluate(
        _features(signal_count_48h=1, strong_signal_count=1, weather_trigger=True)
    )
    assert verdict.band is Band.WARM


def test_one_weak_signal_without_weather_is_a_log_entry():
    verdict = heuristic.evaluate(
        _features(signal_count_48h=1, strong_signal_count=0, weather_trigger=False)
    )
    assert verdict.band is Band.LOG
    assert verdict.route_decision is RouteDecision.DROP


def test_already_customer_is_dropped():
    verdict = heuristic.evaluate(
        _features(signal_count_48h=5, sources=["a"] * 5, already_customer=True)
    )
    assert verdict.route_decision is RouteDecision.DROP
    assert verdict.score == 0.0
    assert any("already a customer" in reason for reason in verdict.reasons)


def test_outside_the_service_area_is_dropped():
    verdict = heuristic.evaluate(
        _features(signal_count_48h=5, sources=["a"] * 5, in_service_area=False)
    )
    assert verdict.route_decision is RouteDecision.DROP
    assert any("outside the service area" in reason for reason in verdict.reasons)


def test_a_duplicate_inside_thirty_days_is_dropped():
    verdict = heuristic.evaluate(
        _features(signal_count_48h=5, sources=["a"] * 5, duplicate_30d=True)
    )
    assert verdict.route_decision is RouteDecision.DROP
    assert any("duplicate" in reason for reason in verdict.reasons)


def test_nothing_routes_without_a_human():
    """No rule emits ROUTE. Every qualifying lead goes to REVIEW."""
    for count in range(0, 6):
        verdict = heuristic.evaluate(
            _features(signal_count_48h=count, sources=["a"] * count)
        )
        assert verdict.route_decision is not RouteDecision.ROUTE


def test_reasons_name_the_signals_used():
    verdict = heuristic.evaluate(
        _features(
            signal_count_48h=2,
            sources=["community_board_post", "public_qa_thread"],
            strong_signal_count=2,
        )
    )
    joined = " ".join(verdict.reasons)
    assert "community_board_post" in joined
    assert "public_qa_thread" in joined


def test_score_stays_inside_the_unit_interval_and_brackets_the_confidence():
    verdict = heuristic.evaluate(
        _features(signal_count_48h=4, sources=["a"] * 4, weather_trigger=True)
    )
    assert 0.0 <= verdict.score <= 1.0
    assert verdict.confidence_lo <= verdict.score <= verdict.confidence_hi


# ---- features -----------------------------------------------------------


def test_events_group_into_one_lead_per_shop_zip_and_trade():
    features = extract_features(sample_signal_events())
    assert len(features) == 3
    assert lead_key("demo-shop-0001", "85001", "HVAC") in features


def test_weather_events_set_the_flag_without_counting_as_demand():
    features = extract_features(sample_signal_events())
    plumbing = features[lead_key("demo-shop-0001", "78701", "plumbing")]

    assert plumbing.weather_trigger is True
    assert plumbing.signal_count_48h == 1
    assert "weather_alert" not in plumbing.sources


def test_signals_older_than_the_window_do_not_count():
    now = datetime.now(timezone.utc)
    stale = SignalEvent(
        source="community_board_post",
        url="https://example.com/demo/old",
        text="DEMO SEED — fictional homeowner, three days ago.",
        zip="85001",
        trade="HVAC",
        observed_at=now - timedelta(hours=72),
        shop_id="demo-shop-0001",
    )
    features = extract_features([stale], now=now)
    assert next(iter(features.values())).signal_count_48h == 0


def test_an_unconfigured_service_area_does_not_drop_everything():
    features = extract_features(sample_signal_events(), service_area_zips=None)
    assert all(item.in_service_area for item in features.values())


def test_an_out_of_area_zip_is_marked():
    features = extract_features(
        sample_signal_events(), service_area_zips={"85001"}
    )
    denver = features[lead_key("demo-shop-0001", "80202", "electrical")]
    assert denver.in_service_area is False


def test_known_customers_and_recent_leads_are_flagged():
    key = lead_key("demo-shop-0001", "85001", "HVAC")
    features = extract_features(sample_signal_events(), known_customers={key})
    assert features[key].already_customer is True

    features = extract_features(sample_signal_events(), recent_lead_keys={key})
    assert features[key].duplicate_30d is True


def test_a_known_customer_is_dropped_end_to_end(tmp_path):
    key = lead_key("demo-shop-0001", "85001", "HVAC")
    result = ingest(
        sample_signal_events(),
        service_area_zips=SAMPLE_SERVICE_AREA_ZIPS,
        zone_map=SAMPLE_ZONE_MAP,
        known_customers={key},
        output_directory=tmp_path,
    )

    customer_lead = next(lead for lead in result.leads if lead.lead_id == key)
    assert customer_lead.route_decision is RouteDecision.DROP
    assert customer_lead.score == 0.0


# ---- the assigner -------------------------------------------------------


def test_assigner_sets_a_zone_without_touching_jobber(monkeypatch, tmp_path):
    called: list[str] = []
    for name in ADAPTER_WRITE_METHODS:
        monkeypatch.setattr(
            JobberAdapter,
            name,
            lambda self, *args, _name=name, **kwargs: called.append(_name),
        )

    leads = run_sample(output_directory=tmp_path).leads

    assert all(lead.suggested_zone for lead in leads)
    assert called == []


def test_explicit_zone_map_wins_over_the_prefix_fallback():
    assert assigner.suggest_zone("85001", SAMPLE_ZONE_MAP) == "demo-zone-north"


def test_an_unmapped_zip_still_gets_a_zone_from_its_prefix():
    assert assigner.suggest_zone("99502", SAMPLE_ZONE_MAP) == "zone-995"


def test_a_missing_zip_has_no_zone():
    assert assigner.suggest_zone("") is None


def test_unit_is_the_first_roster_match_for_the_trade():
    assert assigner.suggest_unit("plumbing") == "DEMO-UNIT-B"
    assert assigner.suggest_unit("HVAC") == "DEMO-UNIT-A"


def test_an_unknown_trade_has_no_unit():
    assert assigner.suggest_unit("underwater-basket-weaving") is None


def test_the_assigner_exposes_no_booking_or_send_surface():
    names = [n for n in dir(assigner) if not n.startswith("_")]
    assert not [
        n
        for n in names
        if any(word in n.lower() for word in ("send", "notify", "book", "schedule"))
    ]


# ---- ranking ------------------------------------------------------------


def test_rank_orders_by_band_then_score():
    def lead(lead_id, band, score):
        return ScoredLead(
            lead_id=lead_id,
            features=_features(),
            score=score,
            band=band,
            confidence_lo=0.0,
            confidence_hi=1.0,
            route_decision=RouteDecision.REVIEW,
        )

    ordered = rank(
        [
            lead("c", Band.LOG, 0.2),
            lead("a", Band.WARM, 0.5),
            lead("b", Band.WARM, 0.7),
            lead("d", Band.HOT, 0.9),
        ]
    )
    assert [item.lead_id for item in ordered] == ["d", "b", "a", "c"]


def test_scoring_without_persisting_still_ranks():
    leads = score_events(
        sample_signal_events(), service_area_zips=SAMPLE_SERVICE_AREA_ZIPS
    )
    assert [lead.band for lead in leads] == [Band.HOT, Band.WARM, Band.LOG]


# ---- the routes ---------------------------------------------------------


def test_ingest_signals_returns_lead_ids_and_an_artifact(client, out_dir):
    events = [event.model_dump(mode="json") for event in sample_signal_events()]

    response = client.post("/ingest/signals", json={"events": events})

    assert response.status_code == 200
    body = response.json()
    assert len(body["lead_ids"]) == 3
    assert body["human_review_required"] is True
    artifact = out_dir / body["artifact_path"].rsplit("/", 1)[-1]
    assert artifact.is_file()


def test_leads_endpoint_returns_a_sorted_list(client):
    events = [event.model_dump(mode="json") for event in sample_signal_events()]
    client.post("/ingest/signals", json={"events": events})

    response = client.get("/leads")

    assert response.status_code == 200
    leads = response.json()
    assert [lead["band"] for lead in leads] == ["hot", "warm", "log"]
    assert all(lead["human_review_required"] for lead in leads)


def test_leads_endpoint_is_empty_before_any_ingest(client):
    response = client.get("/leads")
    assert response.status_code == 200
    assert response.json() == []


def test_ingest_applies_a_batch_shop_id(client):
    events = [
        {
            "source": "community_board_post",
            "url": "https://example.com/demo/x",
            "text": "DEMO SEED — fictional homeowner.",
            "zip": "85001",
            "trade": "HVAC",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    ]

    client.post("/ingest/signals", json={"shop_id": "demo-shop-9", "events": events})

    leads = client.get("/leads").json()
    assert leads[0]["shop_id"] == "demo-shop-9"


def test_the_scoring_routes_add_no_send_or_oauth_surface():
    paths = {route.path for route in api.app.routes}
    forbidden = {"send", "notify", "email", "sms", "oauth", "reply"}
    assert not [p for p in paths if any(word in p.lower() for word in forbidden)]


# ---- CLI ----------------------------------------------------------------


def test_cli_score_sample_prints_the_ranked_list(tmp_path, capsys):
    from nightforge.cli import main

    exit_code = main(["score", "--sample", "--output-dir", str(tmp_path), "--quiet"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "hot" in out and "warm" in out and "log" in out
    assert "demo-zone-north" in out
    assert "Human review: required on every lead" in out
    assert len(list(tmp_path.glob("scored_*.json"))) == 1


def test_cli_score_without_sample_is_refused(capsys):
    from nightforge.cli import main

    assert main(["score", "--quiet"]) == 2
    assert "--sample" in capsys.readouterr().err
