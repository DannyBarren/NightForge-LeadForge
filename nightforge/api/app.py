"""The NightForge FastAPI application. ``GET /health`` only.

Deliberately import-light: no graphs, no vendor SDKs, no LLM clients. Importing
this module must stay cheap enough that a health check can answer while the
rest of the system is still cold.
"""

from __future__ import annotations

from fastapi import FastAPI

from nightforge import __version__

app = FastAPI(
    title="NightForge",
    version=__version__,
    description="Overnight lead research and inbound triage. No send path.",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. Reports nothing that depends on an external service."""
    return {"status": "ok", "version": __version__}
