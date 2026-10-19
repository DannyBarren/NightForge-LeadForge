"""Tests for the learning loop: the outcome store, decisions, delayed job
results, and the refit slot that declines to fit.

Offline throughout. No keys, no model, no vendor call.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from nightforge.adapters.jobber import JobberAdapter
from nightforge.api import app as api
from nightforge.scoring import outcomes
from nightforge.scoring.sample_events import sample_signal_events
from nightforge.scoring.sample_outcomes import sample_outcome_rows
from nightforge.scoring.schema import ClosedOutcome, DecisionLog
from nightforge.scoring.store import OutcomeStore
from nightforge.scoring.train import (
    LIVE_SCORER,
    MIN_ROWS,
    TrainReport,
    train,
    write_metrics,
)

ADAPTER_WRITE_METHODS = (
    "hydrate",
    "find_or_create_client",
    "create_request_or_job",
    "place_draft",
)

STUB_HEADERS = {api.STUB_SIGNATURE_HEADER: api.STUB_SIGNATURE_VALUE}


@pytest.fixture
def store(tmp_path):
    return OutcomeStore(tmp_path / "outcomes.jsonl")


@pytest.fixture
def client(tmp_path, store, monkeypatch):
    monkeypatch.setattr(api, "_output_dir", lambda: tmp_path)
    monkeypatch.setattr(api, "_outcome_store", lambda: store)
    monkeypatch.setattr(api, "_LEADS", {})
    return TestClient(api.app)


@pytest.fixture
def no_adapter_writes(monkeypatch):
    """Trip a flag if anything reaches a Jobber write method."""
    called: list[str] = []
    for name in ADAPTER_WRITE_METHODS:
        monkeypatch.setattr(
            JobberAdapter,
            name,
            lambda self, *args, _name=name, **kwargs: called.append(_name),
        )
    return called


def _ingest_sample(client) -> list[str]:
    events = [event.model_dump(mode="json") for event in sample_signal_events()]
    return client.post("/ingest/signals", json={"events": events}).json()["lead_ids"]


# ---- the store ----------------------------------------------------------


def test_store_round_trips_a_row(store):
    record = DecisionLog(lead_id="lead-1", pursued=True)
    store.append(record)

    rows = store.read_all()
    assert len(rows) == 1
    assert rows[0].lead_id == "lead-1"
    assert rows[0].pursued is True


def test_store_appends_rather_than_overwrites(store):
    for index in range(3):
        store.append(DecisionLog(lead_id=f"lead-{index}", pursued=True))

    assert store.count() == 3
    assert [row.lead_id for row in store.read_all()] == [
        "lead-0",
        "lead-1",
        "lead-2",
    ]


def test_store_writes_one_json_object_per_line(store):
    store.append(DecisionLog(lead_id="lead-1", pursued=True))
    store.append(DecisionLog(lead_id="lead-2", pursued=False))

    lines = store.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["lead_id"] for line in lines)


def test_a_corrupt_line_costs_one_row_not_the_file(store):
    store.append(DecisionLog(lead_id="lead-1", pursued=True))
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    store.append(DecisionLog(lead_id="lead-2", pursued=True))

    rows = store.read_all()
    assert [row.lead_id for row in rows] == ["lead-1", "lead-2"]


def test_a_missing_store_reads_as_empty(tmp_path):
    empty = OutcomeStore(tmp_path / "never-written.jsonl")
    assert empty.is_empty()
    assert empty.read_all() == []


# ---- decisions ----------------------------------------------------------


def test_decision_pursued_true_lands_in_the_store(client, store):
    lead_ids = _ingest_sample(client)

    response = client.post(f"/leads/{lead_ids[0]}/decision", json={"pursued": True})

    assert response.status_code == 200
    assert response.json()["pursued"] is True

    rows = store.read_all()
    assert len(rows) == 1
    assert rows[0].lead_id == lead_ids[0]
    assert rows[0].pursued is True
    assert rows[0].closed is None


def test_decision_pursued_false_also_lands(client, store):
    lead_ids = _ingest_sample(client)

    client.post(f"/leads/{lead_ids[-1]}/decision", json={"pursued": False})

    rows = store.read_all()
    assert len(rows) == 1
    assert rows[0].pursued is False


def test_decision_carries_the_shop_and_suggested_unit(client, store):
    lead_ids = _ingest_sample(client)
    client.post(f"/leads/{lead_ids[0]}/decision", json={"pursued": True})

    row = store.read_all()[0]
    assert row.shop_id == "demo-shop-0001"
    assert row.unit == "DEMO-UNIT-A"


def test_unknown_lead_id_is_a_404(client, store):
    response = client.post("/leads/nope/decision", json={"pursued": True})

    assert response.status_code == 404
    assert store.read_all() == []


def test_a_decision_invokes_no_adapter_write(client, no_adapter_writes):
    lead_ids = _ingest_sample(client)
    client.post(f"/leads/{lead_ids[0]}/decision", json={"pursued": True})
    assert no_adapter_writes == []


# ---- delayed job results ------------------------------------------------


def test_a_won_status_payload_attaches_an_outcome(client, store):
    response = client.post(
        "/webhooks/jobber",
        json={
            "id": "evt-won",
            "status": "won",
            "lead_id": "demo-shop-0001:85001:hvac",
            "revenue": 1800.5,
            "unit": "DEMO-UNIT-A",
        },
        headers=STUB_HEADERS,
    )

    assert response.status_code == 202
    assert response.json()["outcome_recorded"] is True

    rows = store.read_all()
    assert len(rows) == 1
    assert rows[0].closed is ClosedOutcome.WON
    assert rows[0].revenue == 1800.5
    assert rows[0].unit == "DEMO-UNIT-A"
    assert rows[0].pursued is True


def test_a_lost_status_payload_attaches_an_outcome(client, store):
    client.post(
        "/webhooks/jobber",
        json={"id": "evt-lost", "status": "lost", "lead_id": "lead-9"},
        headers=STUB_HEADERS,
    )

    rows = store.read_all()
    assert rows[0].closed is ClosedOutcome.LOST
    assert rows[0].revenue is None


def test_a_status_payload_invokes_no_adapter_write(client, store, no_adapter_writes):
    response = client.post(
        "/webhooks/jobber",
        json={"id": "evt-1", "status": "won", "lead_id": "lead-1", "revenue": 10.0},
        headers=STUB_HEADERS,
    )

    assert response.status_code == 202
    assert no_adapter_writes == []
    assert store.count() == 1


def test_an_ordinary_event_records_no_outcome(client, store):
    response = client.post(
        "/webhooks/jobber", json={"id": "evt-2", "kind": "request"}, headers=STUB_HEADERS
    )

    assert response.status_code == 202
    assert response.json()["outcome_recorded"] is False
    assert store.read_all() == []


def test_an_unrecognised_status_records_nothing(client, store):
    client.post(
        "/webhooks/jobber",
        json={"id": "evt-3", "status": "rescheduled", "lead_id": "lead-1"},
        headers=STUB_HEADERS,
    )
    assert store.read_all() == []


def test_a_nested_status_payload_is_read(client, store):
    client.post(
        "/webhooks/jobber",
        json={"id": "evt-4", "data": {"status": "won", "lead_id": "lead-5"}},
        headers=STUB_HEADERS,
    )

    rows = store.read_all()
    assert rows[0].closed is ClosedOutcome.WON
    assert rows[0].lead_id == "lead-5"


def test_a_result_without_a_lead_id_is_kept_but_marked_unattached(client, store):
    client.post(
        "/webhooks/jobber",
        json={"id": "evt-orphan", "status": "won", "revenue": 500.0},
        headers=STUB_HEADERS,
    )

    rows = store.read_all()
    assert rows[0].lead_id.startswith(outcomes.UNATTACHED_PREFIX)
    assert rows[0].closed is ClosedOutcome.WON


def test_verification_still_gates_the_outcome_path(client, store):
    """A rejected webhook must not write a label."""
    response = client.post(
        "/webhooks/jobber", json={"id": "evt-5", "status": "won", "lead_id": "x"}
    )

    assert response.status_code == 401
    assert store.read_all() == []


def test_the_stub_header_path_still_works(client):
    response = client.post(
        "/webhooks/jobber", json={"id": "evt-6"}, headers=STUB_HEADERS
    )
    assert response.status_code == 202


def test_extract_job_outcome_ignores_a_non_result():
    assert outcomes.extract_job_outcome({"kind": "request"}) is None
    assert outcomes.extract_job_outcome({}) is None


def test_record_job_result_forces_pursued_true(store):
    record = outcomes.record_job_result("lead-1", "won", store=store, revenue=5.0)
    assert record.pursued is True


# ---- the refit slot -----------------------------------------------------


def test_train_sample_writes_metrics_and_skips_the_fit(tmp_path, store):
    report = train(store=store, sample_fallback=True)
    path = write_metrics(report, output_directory=tmp_path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "train_metrics.json"
    assert payload["fitted"] is False
    assert payload["live_scorer"] == "heuristic"
    assert payload["skip_reason"]
    assert payload["rows"] == 3


def test_train_falls_back_to_samples_only_when_the_store_is_empty(store):
    assert train(store=store, sample_fallback=True).source == "sample"

    store.append(DecisionLog(lead_id="real-1", pursued=True))
    report = train(store=store, sample_fallback=True)
    assert report.source == "store"
    assert report.rows == 1


def test_train_without_the_sample_fallback_sees_an_empty_store(store):
    report = train(store=store, sample_fallback=False)
    assert report.rows == 0
    assert report.fitted is False


def test_only_closed_rows_count_as_labels(store):
    store.append(DecisionLog(lead_id="a", pursued=True))
    store.append(
        DecisionLog(lead_id="b", pursued=True, closed=ClosedOutcome.WON, revenue=1.0)
    )

    report = train(store=store)
    assert report.rows == 2
    assert report.labeled_rows == 1


def test_enough_labels_still_does_not_fit(store):
    """The threshold is not the only guard — there is no estimator either."""
    moment = datetime.now(timezone.utc)
    for index in range(MIN_ROWS + 5):
        store.append(
            DecisionLog(
                lead_id=f"lead-{index}",
                pursued=True,
                closed=ClosedOutcome.WON,
                decided_at=moment - timedelta(days=index),
            )
        )

    report = train(store=store)
    assert report.labeled_rows >= MIN_ROWS
    assert report.fitted is False
    assert report.live_scorer == LIVE_SCORER
    assert "no estimator" in report.skip_reason


def test_the_sample_labels_are_deliberately_below_the_threshold():
    labeled = [row for row in sample_outcome_rows() if row.closed is not None]
    assert 0 < len(labeled) < MIN_ROWS


def test_train_report_payload_has_the_required_keys():
    payload = TrainReport(
        rows=0,
        labeled_rows=0,
        fitted=False,
        live_scorer=LIVE_SCORER,
        skip_reason="none",
        source="store",
    ).to_payload()
    assert {"rows", "fitted", "live_scorer", "skip_reason"} <= set(payload)


# ---- CLI ----------------------------------------------------------------


def test_cli_train_sample_exits_zero_when_it_skips(tmp_path, capsys):
    from nightforge.cli import main

    exit_code = main(
        [
            "train",
            "--sample",
            "--store",
            str(tmp_path / "empty.jsonl"),
            "--output-dir",
            str(tmp_path),
            "--quiet",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Fitted: False" in out
    assert "Live scorer: heuristic" in out

    payload = json.loads((tmp_path / "train_metrics.json").read_text(encoding="utf-8"))
    assert payload["fitted"] is False
    assert payload["live_scorer"] == "heuristic"


# ---- the live ranker is unchanged ---------------------------------------


def test_the_live_scorer_is_still_the_heuristic(client):
    """Recording outcomes must not change how leads are ranked."""
    _ingest_sample(client)
    before = [lead["band"] for lead in client.get("/leads").json()]

    lead_ids = _ingest_sample(client)
    client.post(f"/leads/{lead_ids[0]}/decision", json={"pursued": True})

    after = [lead["band"] for lead in client.get("/leads").json()]
    assert before == after == ["hot", "warm", "log"]
