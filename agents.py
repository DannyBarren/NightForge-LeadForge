"""Root shim — see leadforge/agents.py for implementations."""

from leadforge.agents import (
    lead_discovery_agent,
    pitch_strategist_agent,
    research_agent,
)

__all__ = [
    "lead_discovery_agent",
    "research_agent",
    "pitch_strategist_agent",
]
