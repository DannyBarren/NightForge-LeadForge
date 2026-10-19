"""The refit slot. It counts labels and, for now, always declines to fit.

Two independent reasons it declines, and both are reported rather than hidden:

1. There are fewer than ``MIN_ROWS`` labelled outcomes. Fitting a ranker on a
   handful of rows produces a model that is confidently wrong about a business
   someone depends on.
2. Even with enough rows, no estimator is wired in. The live scorer is the
   heuristic in ``nightforge.scoring.heuristic`` and stays that way.

``fitted`` is therefore always ``False`` in this build, and ``live_scorer`` is
always ``"heuristic"``. When an estimator does land, the shape of this report
should not have to change — only the values.

A skipped fit is a success. The command exits 0, because "not enough data yet"
is the expected state for a long time and should not read as a failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from leadforge.config_loader import get_config
from leadforge.export import output_dir
from nightforge.scoring.sample_outcomes import sample_outcome_rows
from nightforge.scoring.schema import ClosedOutcome, DecisionLog
from nightforge.scoring.store import OutcomeStore

# Below this many labelled rows, do not fit anything.
MIN_ROWS = 50

# What actually ranks leads in production. Not a placeholder.
LIVE_SCORER = "heuristic"

METRICS_FILENAME = "train_metrics.json"


@dataclass
class TrainReport:
    """The outcome of a refit attempt."""

    rows: int
    labeled_rows: int
    fitted: bool
    live_scorer: str
    skip_reason: str
    source: str
    min_rows: int = MIN_ROWS
    label_counts: dict[str, int] = field(default_factory=dict)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "fitted": self.fitted,
            "live_scorer": self.live_scorer,
            "skip_reason": self.skip_reason,
            "labeled_rows": self.labeled_rows,
            "min_rows": self.min_rows,
            "label_counts": self.label_counts,
            "source": self.source,
            "generated_at": self.generated_at,
        }


def is_labeled(row: DecisionLog) -> bool:
    """A row can train something only once the job actually closed.

    A pursue/reject decision on its own says what a person believed, not what
    happened. Only ``won`` or ``lost`` is ground truth.
    """
    return row.closed is not None


def label_counts(rows: Sequence[DecisionLog]) -> dict[str, int]:
    counts = {outcome.value: 0 for outcome in ClosedOutcome}
    counts["undecided"] = 0
    for row in rows:
        counts[row.closed.value if row.closed else "undecided"] += 1
    return counts


def load_rows(
    store: OutcomeStore, *, sample_fallback: bool = False
) -> tuple[list[DecisionLog], str]:
    """Live rows, or the checked-in demo labels when the store is empty."""
    rows = store.read_all()
    if rows:
        return rows, "store"
    if sample_fallback:
        return sample_outcome_rows(), "sample"
    return [], "store"


def train(
    *, store: OutcomeStore | None = None, sample_fallback: bool = False
) -> TrainReport:
    """Count the labels and decide whether a fit is warranted. It is not, yet."""
    resolved_store = store or OutcomeStore()
    rows, source = load_rows(resolved_store, sample_fallback=sample_fallback)
    labeled = [row for row in rows if is_labeled(row)]

    if len(labeled) < MIN_ROWS:
        skip_reason = (
            f"only {len(labeled)} labelled row(s); {MIN_ROWS} needed before a fit "
            "is worth trusting"
        )
    else:
        skip_reason = (
            "no estimator is wired in; the live scorer stays the heuristic by "
            "design in this build"
        )

    return TrainReport(
        rows=len(rows),
        labeled_rows=len(labeled),
        fitted=False,
        live_scorer=LIVE_SCORER,
        skip_reason=skip_reason,
        source=source,
        label_counts=label_counts(rows),
    )


def write_metrics(
    report: TrainReport, *, output_directory: Path | str | None = None
) -> Path:
    """Write ``train_metrics.json`` and return its path."""
    if output_directory is not None:
        target = Path(output_directory)
        target.mkdir(parents=True, exist_ok=True)
    else:
        target = output_dir(get_config())

    path = target / METRICS_FILENAME
    path.write_text(json.dumps(report.to_payload(), indent=2), encoding="utf-8")
    return path
