"""Budget governor — interface only. No logic yet.

The governor is the port target for ``leadforge.cost_tracker.CostTracker`` and
``leadforge.guardrails.Guardrails``. Those two are not being rewritten; their
behavior moves here intact:

- Two independent stop thresholds — actual spend at or above ``budget_usd``, or
  projected spend at or above ``min(stop_projected_usd, budget_usd)``.
- A lock, because research runs concurrently and every read of the running
  total has to see the same number every writer does.
- A tripped run is a clean stop: mark it, export what completed, do not crash.

Two things change in the port. Paid search calls go through ``can_afford`` and
``record`` the same as LLM calls — in the prototype, Tavily and Brave spend is
invisible to the cap, which makes the reported cost LLM-tokens-only. And every
method fails closed: if the governor cannot confirm budget, the caller does not
get to make the call.

Implementations must be safe to call from multiple threads.
"""

from __future__ import annotations

from typing import NamedTuple, Protocol, runtime_checkable


class Decision(NamedTuple):
    """Outcome of a budget check.

    ``allowed`` is the only field callers should branch on. ``reason`` is
    human-readable and non-empty whenever ``allowed`` is ``False``; it ends up
    in the run manifest's ``stop_reason``.
    """

    allowed: bool
    reason: str = ""


class GovernorTripped(Exception):
    """Raised when a call is attempted against a tripped or exhausted budget."""


@runtime_checkable
class Governor(Protocol):
    """Gate in front of every LLM call and every paid search call."""

    def can_afford(self, estimated_input: int, estimated_output: int) -> Decision:
        """Pre-call gate. Ask before spending, not after.

        Returns a denying ``Decision`` rather than raising, so callers can stop
        the queue cleanly. Must deny when the budget cannot be confirmed.
        """
        raise NotImplementedError

    def record(
        self,
        agent: str,
        phase: str,
        input_tokens: int,
        output_tokens: int,
        *,
        model: str | None = None,
        lead: str | None = None,
    ) -> float:
        """Book actual usage after a call and return its estimated cost in USD.

        An unmetered call still has to cost something on the books; a provider
        that reports no usage gets a conservative estimate, never zero.
        """
        raise NotImplementedError

    def should_stop(self) -> Decision:
        """Post-call check. ``allowed`` is ``False`` once the run must stop."""
        raise NotImplementedError

    def trip_breaker(self, reason: str) -> None:
        """Latch the run into the stopped state. Not reversible for that run."""
        raise NotImplementedError
