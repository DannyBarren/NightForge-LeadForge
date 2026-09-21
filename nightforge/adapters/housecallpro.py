"""Housecall Pro adapter — stub. Every method raises ``NotImplementedError``.

No credentials for Housecall Pro exist in this repository. The stub exists so
the protocol has a second implementor from the start, which keeps the interface
from quietly shaping itself around one vendor.
"""

from __future__ import annotations

from typing import Any, Mapping

from nightforge.adapters.base import AdapterHealth

_UNIMPLEMENTED = (
    "Housecall Pro adapter is a stub — no credentials are configured."
)


class HousecallProAdapter:
    """Unimplemented ``FieldServiceAdapter`` for Housecall Pro."""

    vendor: str = "housecallpro"

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
