"""Tests for Jobber webhook verification.

No credentials are involved. The secret below is a test fixture invented here
and used only to compute a signature the adapter should accept.
"""

import base64
import hashlib
import hmac

import pytest

from nightforge.adapters.base import FieldServiceAdapter
from nightforge.adapters.jobber import (
    WEBHOOK_SECRET_ENV,
    WEBHOOK_SIGNATURE_HEADER,
    JobberAdapter,
)

TEST_SECRET = "test-secret-not-a-credential"
BODY = b'{"id":"evt-1","kind":"request"}'


def _signature(body: bytes, secret: str = TEST_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


@pytest.fixture
def adapter():
    return JobberAdapter()


@pytest.fixture
def no_secret(monkeypatch):
    monkeypatch.delenv(WEBHOOK_SECRET_ENV, raising=False)


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv(WEBHOOK_SECRET_ENV, TEST_SECRET)


# ---- the secret ---------------------------------------------------------


def test_missing_secret_denies(adapter, no_secret):
    assert adapter.verify_webhook(b"{}", {}) is False


def test_missing_secret_denies_even_with_a_signature(adapter, no_secret):
    headers = {WEBHOOK_SIGNATURE_HEADER: _signature(BODY)}
    assert adapter.verify_webhook(BODY, headers) is False


def test_empty_secret_denies(adapter, monkeypatch):
    monkeypatch.setenv(WEBHOOK_SECRET_ENV, "")
    headers = {WEBHOOK_SIGNATURE_HEADER: _signature(BODY)}
    assert adapter.verify_webhook(BODY, headers) is False


def test_missing_secret_does_not_raise(adapter, no_secret):
    """A config gap must not become a 500 on a public endpoint."""
    assert adapter.verify_webhook(BODY, {}) is False


# ---- the header ---------------------------------------------------------


def test_missing_header_denies(adapter, secret):
    assert adapter.verify_webhook(BODY, {}) is False


def test_empty_header_denies(adapter, secret):
    assert adapter.verify_webhook(BODY, {WEBHOOK_SIGNATURE_HEADER: ""}) is False


def test_wrong_signature_denies(adapter, secret):
    headers = {WEBHOOK_SIGNATURE_HEADER: _signature(b'{"id":"someone-else"}')}
    assert adapter.verify_webhook(BODY, headers) is False


def test_signature_from_a_different_secret_denies(adapter, secret):
    headers = {WEBHOOK_SIGNATURE_HEADER: _signature(BODY, "a-different-secret")}
    assert adapter.verify_webhook(BODY, headers) is False


def test_garbage_header_denies_without_raising(adapter, secret):
    for value in ("not-base64!!", "   ", "Ω-non-ascii"):
        assert adapter.verify_webhook(BODY, {WEBHOOK_SIGNATURE_HEADER: value}) is False


# ---- the accept path ----------------------------------------------------


def test_correct_signature_over_the_exact_bytes_passes(adapter, secret):
    headers = {WEBHOOK_SIGNATURE_HEADER: _signature(BODY)}
    assert adapter.verify_webhook(BODY, headers) is True


def test_header_lookup_is_case_insensitive(adapter, secret):
    headers = {WEBHOOK_SIGNATURE_HEADER.lower(): _signature(BODY)}
    assert adapter.verify_webhook(BODY, headers) is True


def test_an_empty_body_still_verifies(adapter, secret):
    assert adapter.verify_webhook(b"", {WEBHOOK_SIGNATURE_HEADER: _signature(b"")}) is True


def test_a_changed_byte_in_the_body_denies(adapter, secret):
    """The signature covers the raw bytes, so re-serialized JSON will not match."""
    headers = {WEBHOOK_SIGNATURE_HEADER: _signature(BODY)}
    reserialized = b'{"id": "evt-1", "kind": "request"}'
    assert adapter.verify_webhook(reserialized, headers) is False


# ---- everything else stays unimplemented --------------------------------


def test_adapter_satisfies_the_protocol(adapter):
    assert isinstance(adapter, FieldServiceAdapter)


def test_no_method_name_contains_send_or_notify():
    names = [n for n in dir(JobberAdapter) if not n.startswith("_")]
    assert not [n for n in names if "send" in n or "notify" in n]


def test_place_draft_still_raises(adapter):
    with pytest.raises(NotImplementedError):
        adapter.place_draft("target-1", {})


def test_the_other_writes_still_raise(adapter):
    with pytest.raises(NotImplementedError):
        adapter.hydrate({})
    with pytest.raises(NotImplementedError):
        adapter.find_or_create_client({})
    with pytest.raises(NotImplementedError):
        adapter.create_request_or_job("client-1", {})


def test_health_is_still_unhealthy_while_unconfigured(adapter, secret):
    assert adapter.health().ok is False
