"""Tests for the nightforge/ scaffold: the app imports, the interface holds."""

import pytest
from fastapi.testclient import TestClient

from nightforge.adapters.base import AdapterHealth, FieldServiceAdapter
from nightforge.adapters.housecallpro import HousecallProAdapter
from nightforge.adapters.jobber import JobberAdapter
from nightforge.api.app import app

ADAPTER_METHODS = (
    "verify_webhook",
    "hydrate",
    "find_or_create_client",
    "create_request_or_job",
    "place_draft",
    "health",
)


def test_health_endpoint_responds():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_adapter_protocol_defines_the_expected_surface():
    for name in ADAPTER_METHODS:
        assert hasattr(FieldServiceAdapter, name)


def test_no_adapter_method_sends_anything():
    """The interface has no send operation, and adding one should fail here."""
    surface = {n for n in dir(FieldServiceAdapter) if not n.startswith("_")}
    assert not {n for n in surface if "send" in n or "notify" in n}


def test_vendor_stubs_satisfy_the_protocol():
    for adapter in (JobberAdapter(), HousecallProAdapter()):
        assert isinstance(adapter, FieldServiceAdapter)


def test_vendor_stubs_refuse_to_act():
    for adapter in (JobberAdapter(), HousecallProAdapter()):
        with pytest.raises(NotImplementedError):
            adapter.place_draft("id", {})


def test_unconfigured_verification_never_passes(monkeypatch):
    """Jobber verifies for real now, so it denies instead of raising."""
    monkeypatch.delenv("JOBBER_WEBHOOK_SECRET", raising=False)
    assert JobberAdapter().verify_webhook(b"{}", {}) is False

    with pytest.raises(NotImplementedError):
        HousecallProAdapter().verify_webhook(b"{}", {})


def test_vendor_stubs_report_unhealthy_rather_than_raising():
    for adapter in (JobberAdapter(), HousecallProAdapter()):
        health = adapter.health()
        assert isinstance(health, AdapterHealth)
        assert health.ok is False
