"""Research graph — placeholder. No nodes, no edges, no agents yet.

This is where the prototype's discovery → parallel research → pitch sequence
becomes a LangGraph. The agents are deliberately not ported in this change.

When nodes do land, three things carry over from ``leadforge``:

- The state below holds the shared Pydantic contracts, not new copies of them.
- Every node that calls an LLM or a paid search API asks the governor first.
- Sample mode still produces three complete rows when search or the LLM flakes,
  via the seed top-up and the deterministic fallback pitch.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph import StateGraph

from leadforge.models import DiscoveredLead, LeadResearch, PitchOutput, RunManifest


def _extend(left: list, right: list) -> list:
    """Reducer for the per-lead lists that parallel research fans out into."""
    return [*left, *right]


class ResearchState(TypedDict, total=False):
    """State threaded through the research graph.

    ``config`` and ``governor`` ride in state rather than being read from a
    process global, so concurrent runs cannot see each other's budgets.
    """

    run_id: str
    sample_mode: bool
    config: Any
    governor: Any
    manifest: RunManifest
    leads: list[DiscoveredLead]
    research: Annotated[list[LeadResearch], _extend]
    pitches: Annotated[list[PitchOutput], _extend]
    stopped_early: bool
    stop_reason: str | None


def build_research_graph() -> StateGraph:
    """Return the empty research graph.

    No nodes or edges are registered yet, so the result is not compilable.
    """
    return StateGraph(ResearchState)
