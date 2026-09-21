"""The field-service adapter interface.

One protocol, implemented once per vendor. Graphs depend on this module and
never on a vendor SDK.

Everything here fails closed. A signature that cannot be verified is rejected,
a client that cannot be resolved is not guessed at, and a missing credential is
an error rather than a silent no-op.

Note what is absent: there is no send operation. ``place_draft`` is the
terminal write, and it parks a draft for a human to review. No adapter method
delivers anything to an end customer.
"""

from __future__ import annotations

from typing import Any, Mapping, NamedTuple, Protocol, runtime_checkable


class AdapterError(Exception):
    """Base class for adapter failures."""


class AdapterNotConfigured(AdapterError):
    """Raised when required credentials or settings are absent.

    Fail closed: an unconfigured adapter refuses to act. It does not degrade
    into a no-op that a caller could mistake for success.
    """


class WebhookRejected(AdapterError):
    """Raised when an inbound payload fails signature verification."""


class AdapterHealth(NamedTuple):
    """Result of a liveness probe against a vendor API."""

    ok: bool
    vendor: str
    detail: str = ""


@runtime_checkable
class FieldServiceAdapter(Protocol):
    """Everything NightForge needs from a field-service vendor."""

    vendor: str

    def verify_webhook(self, payload: bytes, headers: Mapping[str, str]) -> bool:
        """Verify an inbound webhook signature against the raw request body.

        Takes raw bytes, not parsed JSON — re-serializing changes the bytes the
        signature was computed over. Returns ``False`` or raises
        ``WebhookRejected`` on any doubt, including a missing signature header
        or an unconfigured secret. Never returns ``True`` by default.
        """
        raise NotImplementedError

    def hydrate(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Expand a thin webhook event into the full records it references."""
        raise NotImplementedError

    def find_or_create_client(self, client: Mapping[str, Any]) -> str:
        """Resolve a client to a vendor id, creating one only if no match.

        Matching is the caller's risk: returning the wrong id writes a job onto
        someone else's account. Ambiguous matches raise rather than guess.
        """
        raise NotImplementedError

    def create_request_or_job(
        self, client_id: str, details: Mapping[str, Any]
    ) -> str:
        """Create a request or job for a client and return its vendor id."""
        raise NotImplementedError

    def place_draft(self, target_id: str, draft: Mapping[str, Any]) -> str:
        """Attach a draft to a vendor record for human review.

        This is a write, not a send. The draft lands where a person will see it
        and stays inert until that person acts on it. Any implementation that
        delivers a message to an end customer is a bug, not a feature.
        """
        raise NotImplementedError

    def health(self) -> AdapterHealth:
        """Probe the vendor API. Reports failure rather than raising."""
        raise NotImplementedError
