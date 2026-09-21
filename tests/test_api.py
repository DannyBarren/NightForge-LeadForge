"""Tests for the NightForge service edge: health, runs, and the inbound webhook.

Everything runs offline in sample mode. The webhook tests never reach Jobber —
the adapter is a stub and stays one.
"""

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nightforge.adapters.jobber import JobberAdapter
from nightforge.api import app as api

PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_API_KEY",
)

# Everything the adapter could use to touch Jobber. None of it may run.
ADAPTER_WRITE_METHODS = (
    "hydrate",
    "find_or_create_client",
    "create_request_or_job",
    "place_draft",
)

STUB_HEADERS = {api.STUB_SIGNATURE_HEADER: api.STUB_SIGNATURE_VALUE}


@pytest.fixture
def no_keys(monkeypatch):
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_output_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def client(out_dir, no_keys, monkeypatch):
    monkeypatch.setattr(api, "_RUNS", {})
    return TestClient(api.app)


def _rows(out_dir: Path) -> list[dict]:
    paths = list(out_dir.glob("leads_*.csv"))
    assert len(paths) == 1, f"expected one CSV, found {paths}"
    with paths[0].open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# ---- health -------------------------------------------------------------


def test_health_returns_governor_and_adapter_state(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["governor"] == "ready"
    assert body["adapter"] == "jobber-stub"


def test_health_keeps_the_original_fields(client):
    """The probe predates the governor field; existing callers still work."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"]


# ---- research runs ------------------------------------------------------


def test_sample_run_returns_a_run_id_and_csv_path(client, out_dir):
    response = client.post("/runs/research", json={"sample": True})

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"]
    assert body["status"] == "completed"
    assert body["mode"] == "sample"
    assert body["leads_pitched"] == 3

    csv_path = Path(body["artifact_paths"]["csv"])
    assert csv_path.is_file()
    assert csv_path.parent == out_dir


def test_sample_run_csv_requires_human_review_on_every_row(client, out_dir):
    client.post("/runs/research", json={"sample": True})

    rows = _rows(out_dir)
    assert len(rows) == 3
    assert {row["Human Review"] for row in rows} == {"YES"}


def test_run_response_carries_every_artifact(client):
    paths = client.post("/runs/research", json={"sample": True}).json()[
        "artifact_paths"
    ]
    assert set(paths) == {"csv", "json", "manifest", "token_log"}
    for path in paths.values():
        assert Path(path).is_file()


def test_run_lookup_returns_status_and_artifacts(client):
    run_id = client.post("/runs/research", json={"sample": True}).json()["run_id"]

    body = client.get(f"/runs/{run_id}").json()
    assert body["run_id"] == run_id
    assert body["status"] == "completed"
    assert body["artifact_paths"]["csv"]
    assert body["human_review_required"] is True


def test_unknown_run_is_a_404(client):
    assert client.get("/runs/does-not-exist").status_code == 404


def test_a_halted_run_reports_its_stop_reason(client):
    """A production run with no keys fails preflight and halts cleanly."""
    response = client.post("/runs/research", json={"sample": False})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "halted"
    assert "preflight" in body["stop_reason"]
    assert client.get("/health").json()["governor"] == "halted"


def test_budget_and_zip_reach_the_run(client, out_dir):
    response = client.post(
        "/runs/research", json={"sample": True, "zip": "85001", "budget_usd": 1.0}
    )

    assert response.status_code == 200
    manifest = json.loads(
        next(out_dir.glob("manifest_*.json")).read_text(encoding="utf-8")
    )
    assert "85001" in manifest["location"]


# ---- inbound webhook ----------------------------------------------------


def test_webhook_without_a_signature_is_rejected(client, out_dir):
    response = client.post("/webhooks/jobber", json={"id": "evt-1"})

    assert response.status_code == 401
    assert list(out_dir.glob("inbound_*.json")) == []


def test_webhook_with_a_wrong_stub_value_is_rejected(client):
    response = client.post(
        "/webhooks/jobber",
        json={"id": "evt-1"},
        headers={api.STUB_SIGNATURE_HEADER: "nope"},
    )
    assert response.status_code == 401


def test_webhook_with_the_stub_header_is_accepted_and_stored(client, out_dir):
    response = client.post(
        "/webhooks/jobber", json={"id": "evt-42", "kind": "request"}, headers=STUB_HEADERS
    )

    assert response.status_code == 202
    body = response.json()
    assert body["event_id"] == "evt-42"
    assert body["disposition"] == "stored_for_human_review"

    stored = json.loads(Path(body["stored_at"]).read_text(encoding="utf-8"))
    assert stored["source"] == "jobber"
    assert stored["human_review_required"] is True
    assert stored["payload"]["kind"] == "request"


def test_accepted_webhook_invokes_no_adapter_write_path(client, monkeypatch):
    called: list[str] = []
    for name in ADAPTER_WRITE_METHODS:
        monkeypatch.setattr(
            JobberAdapter,
            name,
            lambda self, *args, _name=name, **kwargs: called.append(_name),
        )

    response = client.post(
        "/webhooks/jobber", json={"id": "evt-7"}, headers=STUB_HEADERS
    )

    assert response.status_code == 202
    assert called == []


def test_jobber_write_methods_are_still_unimplemented():
    adapter = JobberAdapter()
    calls = {
        "hydrate": ({},),
        "find_or_create_client": ({},),
        "create_request_or_job": ("client-1", {}),
        "place_draft": ("target-1", {}),
    }
    for name in ADAPTER_WRITE_METHODS:
        with pytest.raises(NotImplementedError):
            getattr(adapter, name)(*calls[name])
    assert adapter.health().ok is False


def test_event_id_from_the_body_cannot_escape_the_output_directory(client, out_dir):
    response = client.post(
        "/webhooks/jobber", json={"id": "../../etc/passwd"}, headers=STUB_HEADERS
    )

    assert response.status_code == 202
    stored = Path(response.json()["stored_at"])
    assert stored.parent == out_dir
    assert stored.is_file()


def test_a_body_without_an_id_still_stores(client, out_dir):
    response = client.post("/webhooks/jobber", json={"kind": "quote"}, headers=STUB_HEADERS)

    assert response.status_code == 202
    assert len(list(out_dir.glob("inbound_*.json"))) == 1


# ---- the routes that must not exist -------------------------------------


def test_the_app_exposes_no_send_or_oauth_routes():
    paths = {route.path for route in api.app.routes}
    forbidden = {"send", "notify", "email", "sms", "oauth", "reply"}
    assert not [p for p in paths if any(word in p.lower() for word in forbidden)]


def test_the_expected_routes_are_the_only_nightforge_routes():
    paths = {
        route.path
        for route in api.app.routes
        if not route.path.startswith(("/openapi", "/docs", "/redoc"))
    }
    assert paths == {"/health", "/runs/research", "/runs/{run_id}", "/webhooks/jobber"}


def test_the_adapter_surface_has_no_send_operation():
    assert not [
        name
        for name in dir(JobberAdapter)
        if not name.startswith("_") and ("send" in name or "notify" in name)
    ]


# ---- CLI edge -----------------------------------------------------------


def test_dry_run_reports_failure_without_keys(no_keys, capsys):
    from nightforge.cli import main

    exit_code = main(["research", "--dry-run", "--sample", "--quiet"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Status: FAILED" in out
    assert "Send path: none" in out


def test_dry_run_passes_with_a_provider_key_and_spends_nothing(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder-never-called")

    from nightforge.cli import main

    exit_code = main(["research", "--dry-run", "--sample", "--quiet"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Status: OK" in out
    assert "Discovery gate: OK" in out


def test_serve_launches_uvicorn_against_the_app(monkeypatch):
    import uvicorn

    from nightforge.cli import main

    captured: dict = {}
    monkeypatch.setattr(
        uvicorn, "run", lambda target, **kwargs: captured.update(target=target, **kwargs)
    )

    assert main(["serve", "--port", "9123", "--no-reload"]) == 0
    assert captured["target"] == "nightforge.api.app:app"
    assert captured["port"] == 9123
    assert captured["reload"] is False
