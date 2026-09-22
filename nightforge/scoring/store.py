"""Append-only outcome store: one JSON object per line under ``data/outcomes/``.

JSONL because outcomes arrive one at a time, months apart, from two different
places — a human clicking accept and a job closing later. Append-only means a
crash mid-write costs the last line rather than the file, and a corrupt line
costs one row rather than the training set.

Live files are gitignored. The only outcomes in the repository are the demo
labels in ``sample_outcomes``.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Iterable, Iterator

from leadforge.config_loader import _project_root
from nightforge.scoring.schema import DecisionLog

logger = logging.getLogger(__name__)

OUTCOMES_DIRNAME = "outcomes"
OUTCOMES_FILENAME = "outcomes.jsonl"


def default_store_path() -> Path:
    return _project_root() / "data" / OUTCOMES_DIRNAME / OUTCOMES_FILENAME


class OutcomeStore:
    """Append-only JSONL of :class:`DecisionLog` rows. Thread-safe."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_store_path()
        self._lock = threading.Lock()

    def append(self, record: DecisionLog) -> DecisionLog:
        """Write one row and return it. Creates the directory on first write."""
        line = json.dumps(record.model_dump(mode="json"), sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return record

    def extend(self, records: Iterable[DecisionLog]) -> int:
        return sum(1 for record in records if self.append(record))

    def __iter__(self) -> Iterator[DecisionLog]:
        return iter(self.read_all())

    def read_all(self) -> list[DecisionLog]:
        """Every readable row, oldest first.

        A malformed line is logged and skipped rather than raising. One bad
        append should not make the whole history unreadable — but it is logged
        loudly, because silently training on a truncated set is worse than
        knowing the set is short.
        """
        if not self.path.is_file():
            return []

        rows: list[DecisionLog] = []
        with self.path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    rows.append(DecisionLog.model_validate_json(text))
                except ValueError as exc:
                    logger.warning(
                        "Skipping unreadable outcome row %s:%s — %s",
                        self.path,
                        number,
                        exc,
                    )
        return rows

    def count(self) -> int:
        return len(self.read_all())

    def is_empty(self) -> bool:
        return not self.path.is_file() or self.count() == 0
