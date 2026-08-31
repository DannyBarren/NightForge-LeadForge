#!/usr/bin/env python3
"""
Overnight runner for LeadForge — invoke from cron, CI, or a live demo.

Usage:
  python scripts/run_overnight.py --dry-run          # config + preflight only
  python scripts/run_overnight.py --sample           # 3 leads end-to-end (~$0.50)
  python scripts/run_overnight.py --max-leads 10     # cap pipeline
  python scripts/run_overnight.py                    # full overnight run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _print_preflight(cfg) -> bool:
    from leadforge.preflight import run_preflight

    result = run_preflight(cfg)
    print("=== LeadForge preflight ===")
    for w in result.warnings:
        print(f"  WARN:  {w}")
    for e in result.errors:
        print(f"  ERROR: {e}")
    print(f"  Status: {'OK' if result.ok else 'FAILED'}")
    return result.ok


def _print_stop_summary(summary: dict) -> None:
    """Print a clean, human-readable summary for an early stop (no traceback)."""
    print()
    print("=== LeadForge stopped early ===")
    print(f"  Reason:      {summary.get('stop_reason') or 'unknown'}")
    print(f"  Discovered:  {summary.get('leads_discovered', 0)}")
    print(f"  Researched:  {summary.get('leads_researched', 0)}")
    print(f"  Pitched:     {summary.get('leads_pitched', 0)}")
    print(f"  Est. cost:   ${summary.get('estimated_cost_usd', 0):.4f}")
    paths = summary.get("export_paths", {})
    if paths.get("csv"):
        print(f"  CSV:         {paths['csv']}")
    print("  Note: this is a safe budget/guardrail stop, not a crash.")


def main() -> int:
    parser = argparse.ArgumentParser(description="NightForge LeadForge overnight runner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config, API keys, and guardrails (no LLM calls)",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="End-to-end run limited to 3 leads (safe/cheap validation)",
    )
    parser.add_argument(
        "--max-leads",
        type=int,
        default=None,
        metavar="N",
        help="Cap leads processed (also lowers discovery target)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from dotenv import load_dotenv
    from leadforge.config_loader import get_config, resolve_industries, resolve_location
    from leadforge.run_context import RunOptions

    load_dotenv(ROOT / ".env")
    cfg = get_config()

    if args.dry_run:
        print("=== LeadForge dry run (config only) ===")
        print(f"Location: {resolve_location(cfg)}")
        print(f"Industries: {resolve_industries(cfg)}")
        print(f"Model: {cfg.llm.provider}/{cfg.llm.model}")
        print(f"Max leads: {cfg.guardrails.max_leads_processed}")
        print(f"Budget: ${cfg.guardrails.budget_usd} (early stop @ ${cfg.guardrails.stop_projected_usd})")
        print(f"Parallel research: {cfg.guardrails.max_parallel_research}")
        ok = _print_preflight(cfg)
        if not ok:
            print("\nDry run FAILED preflight. Fix the ERROR line(s) above and re-run.")
        return 0 if ok else 1

    options = RunOptions(
        sample_mode=args.sample,
        max_leads=args.max_leads,
    )
    if args.sample:
        logging.info("Sample mode: up to 3 leads end-to-end (parallel research capped at 4)")

    from leadforge.main import run_pipeline
    from leadforge.preflight import run_preflight

    applied = options.apply_to_config(cfg)
    preflight = run_preflight(applied)
    for w in preflight.warnings:
        logging.warning("Preflight: %s", w)
    if not preflight.ok:
        print("\n=== LeadForge preflight FAILED ===")
        for e in preflight.errors:
            print(f"  ERROR: {e}")
        print("Fix the item(s) above and re-run. No LLM calls were made.")
        return 1

    try:
        summary = run_pipeline(options)
    except Exception as exc:
        # Unexpected, non-guardrail failure — keep it readable but informative.
        logging.error("Pipeline failed unexpectedly: %s", exc)
        print("\n=== LeadForge failed ===")
        print(f"  {type(exc).__name__}: {exc}")
        print("  Check API keys, search quotas, and network access, then re-run.")
        return 1

    cost = summary.get("estimated_cost_usd", 0)
    if summary.get("stopped_early"):
        _print_stop_summary(summary)

    if cost > 10.0:
        logging.error("Cost %.2f exceeded $10 hard cap", cost)
        return 1

    pitched = summary.get("leads_pitched", 0)
    if pitched == 0:
        print("\n=== LeadForge produced no pitches ===")
        print("  Check API keys and search quotas, then re-run.")
        return 1

    paths = summary.get("export_paths", {})
    csv_path = paths.get("csv") or paths.get("json") or "(no export)"

    if args.sample:
        print()
        print("=" * 60)
        print(f"DEMO READY: {pitched} lead(s) | est. cost ${cost:.4f}")
        print(f"  CSV:  {csv_path}")
        if paths.get("json"):
            print(f"  JSON: {paths['json']}")
        if summary.get("token_log"):
            print(f"  Cost log: {summary['token_log']}")
        print("  Every row is flagged Human Review: YES. No emails are sent.")
        print("=" * 60)
    else:
        logging.info("Success: %s pitches, est. cost $%.4f -> %s", pitched, cost, csv_path)

    return 0


def run_preflight_ok(cfg) -> bool:
    from leadforge.preflight import run_preflight

    return run_preflight(cfg).ok


if __name__ == "__main__":
    _rc = main()
    # CrewAI/telemetry leave non-daemon background threads running that can
    # stall normal interpreter shutdown for a long time. Output files and the
    # summary are already written above, so flush and exit promptly for a clean
    # CLI/demo experience.
    import os as _os

    sys.stdout.flush()
    sys.stderr.flush()
    _os._exit(_rc)
