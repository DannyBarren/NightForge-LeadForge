"""Jobber adapter — webhook verification only. Every write is still a stub.

``verify_webhook`` implements Jobber's real scheme: the request carries an
``X-Jobber-Hmac-SHA256`` header holding the base64 HMAC-SHA256 of the raw
request body, keyed by the app's client secret.

The secret is read from ``JOBBER_WEBHOOK_SECRET`` at call time and exists only
in the environment. Nothing is committed, nothing is defaulted, and there is no
fallback value — an unset secret means verification denies.

Everything else is unimplemented. There are no OAuth credentials for Jobber in
this repository, no GraphQL client, and no code path that writes back to Jobber
or sends anything to anyone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from typing import Any, Mapping

from nightforge.adapters.base import AdapterHealth

logger = logging.getLogger(__name__)

WEBHOOK_SECRET_ENV = "JOBBER_WEBHOOK_SECRET"
WEBHOOK_SIGNATURE_HEADER = "X-Jobber-Hmac-SHA256"

_UNIMPLEMENTED = "Jobber adapter is a stub — no credentials are configured."


def _header_value(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup.

    Starlette's ``Headers`` is already case-insensitive, but a plain ``dict``
    is not, and both reach this adapter. HTTP header names are case-insensitive
    either way.
    """
    try:
        value = headers.get(name)
        if value is None:
            wanted = name.lower()
            value = next(
                (v for k, v in headers.items() if str(k).lower() == wanted), None
            )
    except (AttributeError, TypeError):
        return ""
    return str(value) if value else ""


def _as_bytes(payload: Any) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, bytearray):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return b""


class JobberAdapter:
    """Jobber ``FieldServiceAdapter``. Verification works; writes do not."""

    vendor: str = "jobber"

    def verify_webhook(self, payload: bytes, headers: Mapping[str, str]) -> bool:
        """Verify ``X-Jobber-Hmac-SHA256`` against the raw request body.

        Fails closed and never raises: a missing secret, a missing header, or a
        mismatch all return ``False``. Raising here would turn a configuration
        gap into a 500 on a public endpoint, and an exception is a far easier
        thing to accidentally catch and treat as success than a ``False`` is.

        The body must be the exact bytes received. Re-serializing parsed JSON
        changes whitespace and key order, and the signature no longer matches.
        """
        secret = os.getenv(WEBHOOK_SECRET_ENV) or ""
        if not secret:
            logger.warning(
                "%s is unset — rejecting the Jobber webhook. Set it to the app's "
                "client secret to enable verification.",
                WEBHOOK_SECRET_ENV,
            )
            return False

        provided = _header_value(headers, WEBHOOK_SIGNATURE_HEADER).strip()
        if not provided:
            return False

        digest = hmac.new(
            secret.encode("utf-8"), _as_bytes(payload), hashlib.sha256
        ).digest()
        expected = base64.b64encode(digest)

        # Compared as bytes so a non-ASCII header value cannot raise.
        return hmac.compare_digest(expected, provided.encode("utf-8"))

    def hydrate(self, event: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(_UNIMPLEMENTED)

    def find_or_create_client(self, client: Mapping[str, Any]) -> str:
        raise NotImplementedError(_UNIMPLEMENTED)

    def create_request_or_job(
        self, client_id: str, details: Mapping[str, Any]
    ) -> str:
        raise NotImplementedError(_UNIMPLEMENTED)

    def place_draft(self, target_id: str, draft: Mapping[str, Any]) -> str:
        raise NotImplementedError(_UNIMPLEMENTED)

    def health(self) -> AdapterHealth:
        """Unhealthy while unconfigured — every write is still unimplemented."""
        return AdapterHealth(ok=False, vendor=self.vendor, detail=_UNIMPLEMENTED)
