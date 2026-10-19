"""Budget governor — the gate in front of every LLM call and every paid search call.

Ported from ``leadforge.cost_tracker.CostTracker`` and
``leadforge.guardrails.Guardrails``. Their behavior moves here intact:

- Two independent stop thresholds — actual spend at or above ``budget_usd``, or
  projected spend at or above ``min(stop_projected_usd, budget_usd)``.
- A lock, because research fans out across threads and every read of the
  running total has to see the same number every writer does.
- A tripped run is a clean stop, not a crash: mark it, export what completed.
- The same per-phase token estimates, so a run stops when it can no longer
  finish rather than after it has already overspent.

Four layers sit on top of the ported behavior:

1. **Per-run cap.** Unchanged from the prototype, plus a separate, much smaller
   default for inbound work, which does a fraction of the LLM work a research
   run does.
2. **Per-day, per-client ceiling.** A run that would exceed it is *deferred* —
   it never starts. That is deliberately different from a per-run halt, which
   stops mid-flight and exports partial results.
3. **Loop protection.** Budget is a poor backstop against a wedged agent: a
   retry loop can burn the iteration budget long before it burns dollars. So
   iterations, tool calls, repeated identical tool calls, and retryable HTTP
   failures each get their own bounded counter.
4. **Fail closed.** Every path that cannot confirm it is safe to proceed
   refuses. A non-retryable HTTP error is not retried, exhausted backoff does
   not fall through to an unguarded call, and a call with no reported usage is
   never booked at zero.

One behavior intentionally differs from the prototype. There, paid search calls
(Tavily, Brave) never touched the cost tracker, so the reported cost was
LLM-tokens-only and search spend was invisible to the cap. Here, search goes
through ``can_afford`` and ``record`` like anything else.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from leadforge.config_loader import AppConfig, _project_root
from leadforge.models import DiscoveredLead

# Per-phase token estimates. Same numbers as leadforge.guardrails — the
# projection math is only meaningful if both pipelines price a phase the same.
EST_DISCOVERY_IN, EST_DISCOVERY_OUT = 3_000, 4_000
EST_RESEARCH_IN, EST_RESEARCH_OUT = 2_500, 2_000
EST_PITCH_IN, EST_PITCH_OUT = 1_800, 1_200

# Fallback $/1M tokens for a model absent from config/settings.yaml. Priced
# well above gpt-4o-mini on purpose: an unknown model should over-report, not
# under-report, so an unpriced model cannot quietly run past the cap.
DEFAULT_PRICING: dict[str, float] = {"input": 0.50, "output": 2.00}

HARD_CEILING_USD = 10.00
RESEARCH_BUDGET_USD = 8.00
RESEARCH_STOP_PROJECTED_USD = 7.25
INBOUND_BUDGET_USD = 2.00

# Safety default for the per-day, per-client ceiling: one research run at the
# hard ceiling. A technical stop-loss, not a commercial limit.
DEFAULT_DAILY_CAP_USD = HARD_CEILING_USD

# Floors, so an unmetered call is never free on the books.
MIN_ESTIMATED_INPUT_TOKENS = 200
MIN_ESTIMATED_OUTPUT_TOKENS = 200
MIN_RECORDED_USD = 0.000001

_RETRYABLE_STATUS = frozenset({408, 425, 429})


class Decision(NamedTuple):
    """Outcome of a budget or admission check.

    ``allowed`` always means *may proceed*, including from ``should_stop``,
    where ``allowed is False`` is the signal to stop. ``reason`` is
    human-readable and non-empty whenever ``allowed`` is ``False``; it ends up
    in the run manifest's ``stop_reason``.
    """

    allowed: bool
    reason: str = ""


class RunKind(str, Enum):
    RESEARCH = "research"
    INBOUND = "inbound"


class GovernorError(Exception):
    """Base class for governor failures."""


class GovernorHalt(GovernorError):
    """The run must stop now.

    Replaces ``leadforge.guardrails.GuardrailViolation`` in this package. It is
    a normal outcome, not a crash: the caller records ``stop_reason`` on the
    manifest, exports whatever completed, and returns.
    """

    def __init__(self, stop_reason: str):
        super().__init__(stop_reason)
        self.stop_reason = stop_reason


class BreakerTripped(GovernorHalt):
    """Loop protection fired. A subclass of halt, so one ``except`` covers both."""


class GovernorDeferred(GovernorError):
    """The run must not start, and should be retried in a later window.

    Distinct from ``GovernorHalt``: nothing has been spent and nothing partial
    exists to export.
    """

    def __init__(self, reason: str, *, retry_after_day: date | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after_day = retry_after_day


def estimate_tokens_from_text(text: str) -> tuple[int, int]:
    """The prototype's chars/4 heuristic, floors included.

    Used when a provider reports no usage. Deliberately crude and deliberately
    not zero — an unmetered call still has to cost something on the books.
    """
    chars = len(text or "")
    return (
        max(chars // 4, MIN_ESTIMATED_INPUT_TOKENS),
        max(chars // 4, MIN_ESTIMATED_OUTPUT_TOKENS),
    )


@dataclass
class UsageRecord:
    """One metered call. Serializes into the ``token_usage_<run_id>.json`` shape."""

    agent: str
    phase: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_usd: float
    lead: str | None = None
    cumulative_usd: float = 0.0
    # True when token counts came from the heuristic rather than the provider.
    estimated: bool = False
    recorded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_log_entry(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "phase": self.phase,
            "lead": self.lead,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_usd": round(self.estimated_usd, 6),
            "cumulative_usd": round(self.cumulative_usd, 6),
            "estimated": self.estimated,
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True)
class BudgetPolicy:
    """Resolved per-run spending limits.

    Construction clamps, so an out-of-range value cannot reach the governor:
    the budget lands in ``(0, HARD_CEILING_USD]`` and the early-stop threshold
    can never exceed it.
    """

    kind: RunKind
    budget_usd: float
    stop_projected_usd: float

    def __post_init__(self) -> None:
        budget = min(max(float(self.budget_usd), 0.01), HARD_CEILING_USD)
        stop = min(max(float(self.stop_projected_usd), 0.01), budget)
        object.__setattr__(self, "budget_usd", budget)
        object.__setattr__(self, "stop_projected_usd", stop)

    @classmethod
    def for_research(cls, config: AppConfig | None = None) -> BudgetPolicy:
        if config is None:
            return cls(
                RunKind.RESEARCH, RESEARCH_BUDGET_USD, RESEARCH_STOP_PROJECTED_USD
            )
        g = config.guardrails
        return cls(RunKind.RESEARCH, g.budget_usd, g.stop_projected_usd)

    @classmethod
    def for_inbound(cls, budget_usd: float = INBOUND_BUDGET_USD) -> BudgetPolicy:
        # No separate early-stop is configured for inbound, so the projected
        # threshold is the budget itself. The projection still includes the
        # upcoming call, so this stops before the overspend, not after.
        return cls(RunKind.INBOUND, budget_usd, budget_usd)


@dataclass(frozen=True)
class LoopLimits:
    """Bounds on how hard a run may spin.

    Iteration and retry defaults come from the prototype: the busiest agent
    runs ``max_iter=10``, and ``leadforge.tools`` retries three times with
    exponential backoff between 2 and 10 seconds.
    """

    max_iterations: int = 25
    max_tool_calls: int = 60
    # The (N+1)th identical tool call trips. Two identical calls can be a
    # legitimate retry; a fourth is a loop.
    max_repeat_tool_calls: int = 3
    max_retry_attempts: int = 3
    retry_base_delay_sec: float = 2.0
    retry_max_delay_sec: float = 10.0


class DailyLedger:
    """Per-day, per-client spend, shared across runs.

    In-memory by default. Pass ``path`` to persist as JSON so the ceiling
    survives a process restart; without it, a restart resets the day's total.

    Thread-safe, and safe to share between governors.
    """

    def __init__(
        self,
        *,
        daily_cap_usd: float = DEFAULT_DAILY_CAP_USD,
        path: Path | str | None = None,
    ):
        self.daily_cap_usd = float(daily_cap_usd)
        self._path = Path(path) if path else None
        self._lock = threading.RLock()
        self._spend: dict[tuple[str, str], float] = {}
        if self._path and self._path.is_file():
            self._load()

    @staticmethod
    def _today() -> date:
        return datetime.now(timezone.utc).date()

    def _key(self, client_id: str, day: date | None) -> tuple[str, str]:
        return (client_id, (day or self._today()).isoformat())

    def _load(self) -> None:
        import json

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # An unreadable ledger must not read as "nothing spent today".
            raise GovernorError(
                f"Daily ledger at {self._path} is unreadable; refusing to start "
                "a run that cannot be accounted against the daily ceiling."
            )
        self._spend = {
            (entry["client_id"], entry["day"]): float(entry["spent_usd"])
            for entry in raw.get("entries", [])
        }

    def _save_locked(self) -> None:
        if not self._path:
            return
        import json

        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "daily_cap_usd": self.daily_cap_usd,
            "entries": [
                {"client_id": c, "day": d, "spent_usd": round(v, 6)}
                for (c, d), v in sorted(self._spend.items())
            ],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def spent(self, client_id: str, *, day: date | None = None) -> float:
        with self._lock:
            return self._spend.get(self._key(client_id, day), 0.0)

    def add(self, client_id: str, usd: float, *, day: date | None = None) -> float:
        with self._lock:
            key = self._key(client_id, day)
            total = self._spend.get(key, 0.0) + float(usd)
            self._spend[key] = total
            self._save_locked()
            return total

    def admit(
        self,
        client_id: str,
        *,
        projected_usd: float = 0.0,
        day: date | None = None,
    ) -> Decision:
        """Decide whether a client may start another run today.

        Denies when today's spend already meets the ceiling, or when the run
        being admitted would carry it past. The caller defers rather than
        processing a run it cannot finish.
        """
        with self._lock:
            spent = self._spend.get(self._key(client_id, day), 0.0)
            cap = self.daily_cap_usd
            if spent >= cap:
                return Decision(
                    False,
                    f"Daily ceiling reached for {client_id}: "
                    f"${spent:.2f} >= ${cap:.2f}",
                )
            if projected_usd and spent + projected_usd > cap:
                return Decision(
                    False,
                    f"Run would exceed the daily ceiling for {client_id}: "
                    f"${spent:.2f} + ${projected_usd:.2f} > ${cap:.2f}",
                )
            return Decision(True)


class Governor:
    """Per-run budget, admission, and loop protection. Thread-safe.

    One instance per run. Share a ``DailyLedger`` across instances to enforce
    the per-day, per-client ceiling.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        model: str | None = None,
        policy: BudgetPolicy | None = None,
        limits: LoopLimits | None = None,
        ledger: DailyLedger | None = None,
        client_id: str = "default",
    ):
        self.config = config
        self.model = model or config.llm.model
        self.policy = policy or BudgetPolicy.for_research(config)
        self.limits = limits or LoopLimits()
        self.ledger = ledger
        self.client_id = client_id

        # Re-entrant: the public checks compose with each other, and a plain
        # lock would deadlock the moment one called another.
        self._lock = threading.RLock()
        self._records: list[UsageRecord] = []
        self._total_input = 0
        self._total_output = 0
        self._stopped = False
        self._stop_reason: str | None = None

        self._iterations = 0
        self._tool_calls = 0
        self._tool_call_counts: dict[tuple[str, str], int] = {}
        self._retry_attempts: dict[str, int] = {}

    # ---- classmethod constructors -------------------------------------

    @classmethod
    def for_research(cls, config: AppConfig, **kwargs: Any) -> Governor:
        return cls(config, policy=BudgetPolicy.for_research(config), **kwargs)

    @classmethod
    def for_inbound(
        cls,
        config: AppConfig,
        *,
        budget_usd: float = INBOUND_BUDGET_USD,
        **kwargs: Any,
    ) -> Governor:
        return cls(config, policy=BudgetPolicy.for_inbound(budget_usd), **kwargs)

    # ---- state --------------------------------------------------------

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    @property
    def stop_reason(self) -> str | None:
        with self._lock:
            return self._stop_reason

    @property
    def records(self) -> list[UsageRecord]:
        with self._lock:
            return list(self._records)

    # ---- layer 1: per-run cap -----------------------------------------

    def pricing(self) -> dict[str, float]:
        return self.config.pricing_usd_per_million.get(self.model, DEFAULT_PRICING)

    def estimate_usd(self, input_tokens: int, output_tokens: int) -> float:
        p = self.pricing()
        return (input_tokens / 1_000_000 * p["input"]) + (
            output_tokens / 1_000_000 * p["output"]
        )

    def total_cost_usd(self) -> float:
        with self._lock:
            return self._total_locked()

    def _total_locked(self) -> float:
        return sum(r.estimated_usd for r in self._records)

    def projected_cost_if(self, extra_input: int, extra_output: int) -> float:
        with self._lock:
            return self._total_locked() + self.estimate_usd(extra_input, extra_output)

    def _budget_decision_locked(self, extra_in: int, extra_out: int) -> Decision:
        actual = self._total_locked()
        budget = self.policy.budget_usd
        early_stop = self.policy.stop_projected_usd
        projected = actual + self.estimate_usd(extra_in, extra_out)

        if actual >= budget:
            return Decision(
                False, f"Hard budget: ${actual:.2f} >= ${budget:.2f}"
            )
        if projected >= early_stop:
            return Decision(
                False, f"Projected ${projected:.2f} >= ${early_stop:.2f}"
            )
        return Decision(True)

    def can_afford(self, estimated_input: int, estimated_output: int) -> Decision:
        """Pre-call gate. Ask before spending, not after.

        Returns a denying ``Decision`` rather than raising, so a caller draining
        a queue can stop cleanly. A run already stopped stays denied.
        """
        with self._lock:
            if self._stopped:
                return Decision(False, self._stop_reason or "Run stopped")
            return self._budget_decision_locked(estimated_input, estimated_output)

    def should_stop(self) -> Decision:
        """Post-call check. ``allowed is False`` means the run must stop.

        Latches: once this decides to stop, the breaker is tripped and the run
        does not recover.
        """
        with self._lock:
            if self._stopped:
                return Decision(False, self._stop_reason or "Run stopped")
            decision = self._budget_decision_locked(0, 0)
            if not decision.allowed:
                self._trip_locked(decision.reason)
            return decision

    def check_budget_before_phase(
        self, phase: str, estimated_in: int, estimated_out: int
    ) -> None:
        """Raising form of ``can_afford``, for callers that cannot continue.

        Trips the breaker and raises ``GovernorHalt`` carrying ``stop_reason``.
        """
        decision = self.can_afford(estimated_in, estimated_out)
        if not decision.allowed:
            reason = f"{phase}: {decision.reason}"
            self.trip_breaker(reason)
            raise GovernorHalt(reason)

    def check_remaining_pipeline(
        self,
        phase: str,
        *,
        leads_left_research: int = 0,
        leads_left_pitch: int = 0,
    ) -> None:
        """Project every *remaining* research and pitch call, then gate on it.

        This is why a run stops while it can still finish cleanly instead of
        discovering mid-queue that it cannot.
        """
        est_in = leads_left_research * EST_RESEARCH_IN + leads_left_pitch * EST_PITCH_IN
        est_out = (
            leads_left_research * EST_RESEARCH_OUT + leads_left_pitch * EST_PITCH_OUT
        )
        self.check_budget_before_phase(phase, est_in, est_out)

    def trip_breaker(self, reason: str) -> None:
        """Latch the run into the stopped state. Not reversible for that run."""
        with self._lock:
            self._trip_locked(reason)

    def _trip_locked(self, reason: str) -> None:
        if not self._stopped:
            self._stopped = True
            self._stop_reason = reason

    # ---- layer 4/5: the ledger ----------------------------------------

    def record(
        self,
        agent: str,
        phase: str,
        input_tokens: int,
        output_tokens: int,
        *,
        model: str | None = None,
        lead: str | None = None,
        estimated: bool = False,
    ) -> float:
        """Book actual usage and return its cost in USD.

        Never books zero. A call reporting no tokens is charged the heuristic
        floor and marked ``estimated``, because a call that happened and cost
        nothing on the books is how a budget quietly stops meaning anything.
        """
        inp = max(int(input_tokens), 0)
        out = max(int(output_tokens), 0)
        if inp == 0 and out == 0:
            inp, out = MIN_ESTIMATED_INPUT_TOKENS, MIN_ESTIMATED_OUTPUT_TOKENS
            estimated = True

        usd = max(self.estimate_usd(inp, out), MIN_RECORDED_USD)

        with self._lock:
            cumulative = self._total_locked() + usd
            self._records.append(
                UsageRecord(
                    agent=agent,
                    phase=phase,
                    model=model or self.model,
                    input_tokens=inp,
                    output_tokens=out,
                    estimated_usd=usd,
                    lead=lead,
                    cumulative_usd=cumulative,
                    estimated=estimated,
                )
            )
            self._total_input += inp
            self._total_output += out

        if self.ledger is not None:
            self.ledger.add(self.client_id, usd)
        return usd

    def record_from_usage(
        self,
        agent: str,
        phase: str,
        usage: Any,
        *,
        fallback_text: str = "",
        model: str | None = None,
        lead: str | None = None,
    ) -> float:
        """Book a call from a provider usage object, however it reports.

        Handles both mapping and attribute styles. When usage is absent or
        empty, falls back to the chars/4 heuristic over ``fallback_text`` and
        marks the record estimated.
        """
        inp = out = 0
        if isinstance(usage, dict):
            inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            out = int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
        elif usage is not None:
            inp = int(
                getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0)
            )
            out = int(
                getattr(usage, "completion_tokens", 0)
                or getattr(usage, "output_tokens", 0)
            )

        if inp == 0 and out == 0:
            inp, out = estimate_tokens_from_text(fallback_text)
            return self.record(
                agent, phase, inp, out, model=model, lead=lead, estimated=True
            )
        return self.record(agent, phase, inp, out, model=model, lead=lead)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            total = self._total_locked()
            budget = self.policy.budget_usd
            return {
                "model": self.model,
                "run_kind": self.policy.kind.value,
                "total_input_tokens": self._total_input,
                "total_output_tokens": self._total_output,
                "estimated_cost_usd": round(total, 4),
                "budget_usd": budget,
                "early_stop_usd": self.policy.stop_projected_usd,
                "budget_remaining_usd": round(max(budget - total, 0.0), 4),
                "budget_used_pct": round(
                    (total / budget * 100) if budget else 0.0, 2
                ),
                "pricing_usd_per_million": self.pricing(),
                "stopped_early": self._stopped,
                "stop_reason": self._stop_reason,
                "records": [r.to_log_entry() for r in self._records],
            }

    def write_log(self, run_id: str, *, out_dir: Path | None = None) -> Path:
        """Write ``token_usage_<run_id>.json``.

        Same filename, location, and keys as the prototype's log, so anything
        reading those files keeps working. The per-record ``estimated`` flag
        and the run-level ``run_kind`` are additions, not replacements.
        """
        import json

        target = out_dir or (_project_root() / "data" / "output")
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"token_usage_{run_id}.json"
        payload = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **self.summary(),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    # ---- layer 2: per-day, per-client ceiling --------------------------

    def admit(self, *, projected_usd: float | None = None) -> Decision:
        """Check the daily ceiling before a run starts.

        No ledger configured means nothing to check. ``projected_usd`` defaults
        to this run's full budget, so admission asks whether the run could
        finish within the ceiling, not merely whether it could begin.
        """
        if self.ledger is None:
            return Decision(True)
        budget = self.policy.budget_usd if projected_usd is None else projected_usd
        return self.ledger.admit(self.client_id, projected_usd=budget)

    def require_admission(self, *, projected_usd: float | None = None) -> None:
        """Raising form of ``admit``. Raises ``GovernorDeferred`` when over cap.

        Defer, do not process: nothing has been spent, so there is nothing
        partial to export and the work is still valid in a later window.
        """
        decision = self.admit(projected_usd=projected_usd)
        if not decision.allowed:
            raise GovernorDeferred(decision.reason)

    # ---- layer 3: loop protection --------------------------------------

    def note_iteration(self) -> None:
        """Count one agent/graph iteration. Trips past ``max_iterations``."""
        with self._lock:
            self._iterations += 1
            if self._iterations > self.limits.max_iterations:
                reason = (
                    f"Iteration limit exceeded: {self._iterations} > "
                    f"{self.limits.max_iterations}"
                )
                self._trip_locked(reason)
                raise BreakerTripped(reason)

    def note_tool_call(self, tool: str, args: Any = None) -> None:
        """Count one tool call, and catch the same call repeating.

        Two bounds: total calls for the run, and identical ``(tool, args)``
        pairs. The repeat bound is the one that catches a wedged agent early —
        it fires long before the budget would, and a loop that re-runs the same
        search with the same arguments is never doing useful work.
        """
        signature = (tool, repr(args))
        with self._lock:
            self._tool_calls += 1
            if self._tool_calls > self.limits.max_tool_calls:
                reason = (
                    f"Tool call limit exceeded: {self._tool_calls} > "
                    f"{self.limits.max_tool_calls}"
                )
                self._trip_locked(reason)
                raise BreakerTripped(reason)

            count = self._tool_call_counts.get(signature, 0) + 1
            self._tool_call_counts[signature] = count
            if count > self.limits.max_repeat_tool_calls:
                reason = (
                    f"Repeated tool call: {tool} called {count} times with "
                    f"identical arguments (limit {self.limits.max_repeat_tool_calls})"
                )
                self._trip_locked(reason)
                raise BreakerTripped(reason)

    @staticmethod
    def is_retryable(status_code: int) -> bool:
        """429, 408, 425, and 5xx are worth another attempt. Nothing else is."""
        return status_code in _RETRYABLE_STATUS or 500 <= status_code < 600

    def backoff_delay(self, attempt: int) -> float:
        """Exponential delay in seconds for a 1-based attempt number.

        Same curve as the prototype's tenacity config: doubling from the base,
        clamped at the ceiling.
        """
        delay = self.limits.retry_base_delay_sec * (2 ** max(attempt - 1, 0))
        return min(delay, self.limits.retry_max_delay_sec)

    def note_retryable_error(self, status_code: int, *, tool: str = "call") -> float:
        """Register a failed vendor call and return how long to wait.

        Fails closed twice over. A non-retryable status is not retried at all,
        and exhausting the bounded attempts trips the breaker rather than
        falling through to another unguarded call. The caller always either
        gets a delay or an exception — never silent permission to continue.
        """
        if not self.is_retryable(status_code):
            reason = f"{tool}: non-retryable HTTP {status_code}"
            self.trip_breaker(reason)
            raise BreakerTripped(reason)

        with self._lock:
            attempts = self._retry_attempts.get(tool, 0) + 1
            self._retry_attempts[tool] = attempts
            if attempts >= self.limits.max_retry_attempts:
                reason = (
                    f"{tool}: HTTP {status_code} after {attempts} attempts "
                    f"(limit {self.limits.max_retry_attempts})"
                )
                self._trip_locked(reason)
                raise BreakerTripped(reason)
            return self.backoff_delay(attempts)

    def reset_retries(self, tool: str = "call") -> None:
        """Clear the retry count for a tool after it succeeds."""
        with self._lock:
            self._retry_attempts.pop(tool, None)

    def loop_state(self) -> dict[str, int]:
        with self._lock:
            return {
                "iterations": self._iterations,
                "tool_calls": self._tool_calls,
                "distinct_tool_signatures": len(self._tool_call_counts),
            }

    # ---- ported Guardrails helpers --------------------------------------

    def cap_leads(self, leads: Iterable[DiscoveredLead]) -> list[DiscoveredLead]:
        """Truncate a discovery list to the configured cap.

        The last line of defense against an agent that returns 200 leads.
        """
        items = list(leads)
        cap = self.config.guardrails.max_leads_processed
        return items[:cap] if len(items) > cap else items

    def max_parallel_research(self) -> int:
        """Worker count, clamped to 1–8.

        Reads config only. The prototype also consulted ``os.getenv`` at call
        time, which lets one run's environment change another's worker count
        mid-flight; env still reaches the value through ``AppConfig``.
        """
        return max(1, min(8, self.config.guardrails.max_parallel_research))

    def human_review_required(self) -> bool:
        """Always ``True``. There is no send path for it to gate."""
        return True
