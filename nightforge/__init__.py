"""NightForge — the production LangGraph + FastAPI package.

This package is being built beside ``leadforge/``, which remains the working
CrewAI + Streamlit prototype. Nothing here replaces it.

Shared contracts (``DiscoveredLead``, ``LeadResearch``, ``PitchOutput``,
``RunManifest``) are imported from ``leadforge.models`` rather than redefined,
so both pipelines export the same shape.

Invariants that hold everywhere in this package:

- No email or SMS send path exists, and none gets added.
- ``Human Review`` on every research export row is ``YES``.
- Public web data only; nothing behind a login wall.
- The governor gates every LLM call and every paid search call, and fails
  closed when it cannot confirm budget.
"""

from __future__ import annotations

__version__ = "0.0.1"
