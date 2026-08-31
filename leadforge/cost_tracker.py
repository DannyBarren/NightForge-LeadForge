"""Thread-safe token usage logging and budget enforcement."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from leadforge.config_loader import AppConfig, _project_root

logger = logging.getLogger(__name__)

DEFAULT_PRICING = {"input": 0.50, "output": 2.00}


@dataclass
class UsageRecord:
    agent: str
    phase: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_usd: float
    lead: str | None = None
    cumulative_usd: float = 0.0
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class CostTracker:
    """Accumulates token estimates; safe for parallel research workers."""

    config: AppConfig
    model: str
    records: list[UsageRecord] = field(default_factory=list)
    total_input: int = 0
    total_output: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _effective_stop_threshold(self) -> float:
        g = self.config.guardrails
        return min(g.stop_projected_usd, g.budget_usd)

    def _pricing(self) -> dict[str, float]:
        table = self.config.pricing_usd_per_million
        return table.get(self.model, DEFAULT_PRICING)

    def estimate_usd(self, input_tokens: int, output_tokens: int) -> float:
        p = self._pricing()
        return (input_tokens / 1_000_000 * p["input"]) + (
            output_tokens / 1_000_000 * p["output"]
        )

    def total_cost_usd(self) -> float:
        with self._lock:
            return sum(r.estimated_usd for r in self.records)

    def projected_cost_if(self, extra_input: int, extra_output: int) -> float:
        return self.total_cost_usd() + self.estimate_usd(extra_input, extra_output)

    def should_stop(
        self, projected_extra_in: int = 0, projected_extra_out: int = 0
    ) -> tuple[bool, str]:
        g = self.config.guardrails
        early_stop = self._effective_stop_threshold()
        projected = self.projected_cost_if(projected_extra_in, projected_extra_out)
        actual = self.total_cost_usd()

        if actual >= g.budget_usd:
            return (
                True,
                f"Hard budget reached: actual ${actual:.2f} >= ${g.budget_usd:.2f}",
            )
        if projected >= early_stop:
            return (
                True,
                f"Projected ${projected:.2f} >= early-stop ${early_stop:.2f}",
            )
        return False, ""

    def can_afford(self, estimated_in: int, estimated_out: int) -> tuple[bool, str]:
        """Check before starting a new agent call (thread-safe)."""
        with self._lock:
            stop, reason = self._should_stop_locked(estimated_in, estimated_out)
        return (not stop, reason)

    def _should_stop_locked(
        self, projected_extra_in: int, projected_extra_out: int
    ) -> tuple[bool, str]:
        g = self.config.guardrails
        early_stop = min(g.stop_projected_usd, g.budget_usd)
        projected = sum(r.estimated_usd for r in self.records) + self.estimate_usd(
            projected_extra_in, projected_extra_out
        )
        actual = sum(r.estimated_usd for r in self.records)
        if actual >= g.budget_usd:
            return True, f"Hard budget: ${actual:.2f} >= ${g.budget_usd:.2f}"
        if projected >= early_stop:
            return True, f"Projected ${projected:.2f} >= ${early_stop:.2f}"
        return False, ""

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
        m = model or self.model
        usd = self.estimate_usd(input_tokens, output_tokens)
        with self._lock:
            total = sum(r.estimated_usd for r in self.records) + usd
            self.records.append(
                UsageRecord(agent, phase, m, input_tokens, output_tokens, usd, lead, total)
            )
            self.total_input += input_tokens
            self.total_output += output_tokens
        logger.info(
            "[%s/%s%s] in=%s out=%s est=$%.4f run=$%.4f",
            agent,
            phase,
            f" @{lead}" if lead else "",
            input_tokens,
            output_tokens,
            usd,
            total,
        )
        return usd

    def record_from_crew_usage(
        self,
        agent: str,
        phase: str,
        usage: Any,
        *,
        model: str | None = None,
        lead: str | None = None,
    ) -> float:
        if usage is None:
            return 0.0
        if isinstance(usage, dict):
            inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            out = int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
        else:
            inp = int(
                getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0)
            )
            out = int(
                getattr(usage, "completion_tokens", 0)
                or getattr(usage, "output_tokens", 0)
            )
        return self.record(agent, phase, inp, out, model=model, lead=lead)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            total = sum(r.estimated_usd for r in self.records)
            budget = self.config.guardrails.budget_usd
            early_stop = min(
                self.config.guardrails.stop_projected_usd,
                self.config.guardrails.budget_usd,
            )
            return {
                "model": self.model,
                "total_input_tokens": self.total_input,
                "total_output_tokens": self.total_output,
                "estimated_cost_usd": round(total, 4),
                "budget_usd": budget,
                "early_stop_usd": early_stop,
                "budget_remaining_usd": round(max(budget - total, 0.0), 4),
                "budget_used_pct": round((total / budget * 100) if budget else 0.0, 2),
                "pricing_usd_per_million": self._pricing(),
                "records": [
                    {
                        "agent": r.agent,
                        "phase": r.phase,
                        "lead": r.lead,
                        "model": r.model,
                        "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens,
                        "estimated_usd": round(r.estimated_usd, 4),
                        "cumulative_usd": round(r.cumulative_usd, 4),
                        "recorded_at": r.recorded_at,
                    }
                    for r in self.records
                ],
            }

    def write_log(self, run_id: str) -> Path:
        out_dir = _project_root() / "data" / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"token_usage_{run_id}.json"
        payload = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **self.summary(),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
