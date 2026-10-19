"""Inbound graph — placeholder. No nodes, no edges yet.

Intended shape: a verified vendor webhook arrives, gets hydrated through an
adapter, and produces a draft parked for a human. Nothing in this graph ever
sends anything; the terminal step writes a draft that a person opens, edits,
and acts on.

Fail-closed rules that apply the moment the first node lands: an unverifiable
webhook signature is rejected rather than processed, and an unresolved identity
is rejected rather than defaulted.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import StateGraph


class InboundState(TypedDict, total=False):
    """State threaded through the inbound graph."""

    event_id: str
    source: str
    signature_verified: bool
    raw_event: dict[str, Any]
    hydrated: dict[str, Any]
    adapter: Any
    draft_id: str | None
    rejected_reason: str | None


def build_inbound_graph() -> StateGraph:
    """Return the empty inbound graph.

    No nodes or edges are registered yet, so the result is not compilable.
    """
    return StateGraph(InboundState)
