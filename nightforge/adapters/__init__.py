"""Vendor adapters.

Every field-service vendor API sits behind the ``FieldServiceAdapter`` protocol
in ``nightforge.adapters.base``. Graph and node code imports the protocol, never
a vendor SDK, so the vendor stays swappable and the graphs stay testable with a
fake.

Both concrete adapters are unimplemented stubs. No credentials for either
vendor exist in this repository.
"""

from __future__ import annotations

from nightforge.adapters.base import (
    AdapterError,
    AdapterHealth,
    AdapterNotConfigured,
    FieldServiceAdapter,
    WebhookRejected,
)

__all__ = [
    "AdapterError",
    "AdapterHealth",
    "AdapterNotConfigured",
    "FieldServiceAdapter",
    "WebhookRejected",
]
