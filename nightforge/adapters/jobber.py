"""Jobber adapter — stub. Every method raises ``NotImplementedError``.

No OAuth credentials for Jobber exist in this repository and none are invented
here. Wiring this up needs a real app registration held outside the repo; until
that exists, the stub is the honest state of things.

The stub is still useful: graphs can be written and tested against the
``FieldServiceAdapter`` protocol without waiting for it.
"""

from __future__ import annotations

from typing import Any, Mapping

from nightforge.adapters.base import AdapterHealth

_UNIMPLEMENTED = "Jobber adapter is a stub — no credentials are configured."


class JobberAdapter:
    """Unimplemented ``FieldServiceAdapter`` for Jobber."""

    vendor: str = "jobber"

    def verify_webhook(self, payload: bytes, headers: Mapping[str, str]) -> bool:
        raise NotImplementedError(_UNIMPLEMENTED)

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
        return AdapterHealth(ok=False, vendor=self.vendor, detail=_UNIMPLEMENTED)
