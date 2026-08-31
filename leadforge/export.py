"""Export leads to JSON, CSV, and optional Google Sheets."""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from leadforge.config_loader import AppConfig, _project_root
from leadforge.models import PitchOutput, RunManifest

logger = logging.getLogger(__name__)

EXPORT_COLUMNS = [
    "Company",
    "Owner",
    "Email",
    "Phone",
    "LinkedIn",
    "Industry",
    "Location",
    "Lead Scope",
    "Pains",
    "Barren Fit",
    "Draft Email",
    "Pitch Angle",
    "Specific Value Props",
    "Recommended Next Step",
    "Confidence",
    "Human Review",
    "Sources",
]


def output_dir(cfg: AppConfig) -> Path:
    rel = cfg.export.get("output_dir", "data/output")
    path = _project_root() / rel
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_pitches(
    pitches: Iterable[PitchOutput],
    manifest: RunManifest,
    cfg: AppConfig,
) -> dict[str, Path]:
    out = output_dir(cfg)
    stamp = manifest.run_id
    rows = [p.to_export_row() for p in pitches]

    paths: dict[str, Path] = {}
    fmt = cfg.export.get("format", "both")

    if fmt in ("json", "both"):
        p = out / f"leads_{stamp}.json"
        write_json(p, rows)
        paths["json"] = p

    if fmt in ("csv", "both"):
        p = out / f"leads_{stamp}.csv"
        write_csv(p, rows)
        paths["csv"] = p

    manifest_path = out / f"manifest_{stamp}.json"
    manifest.finished_at = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    paths["manifest"] = manifest_path

    if fmt == "sheets" or os.getenv("GOOGLE_SHEET_ID"):
        _try_sheets_append(rows)

    logger.info("Exported %s leads to %s", len(rows), paths)
    return paths


def _try_sheets_append(rows: list[dict]) -> None:
    from leadforge.tools import GoogleSheetsAppendTool

    tool = GoogleSheetsAppendTool()
    result = tool._run(json.dumps(rows))
    logger.info("Google Sheets: %s", result)
