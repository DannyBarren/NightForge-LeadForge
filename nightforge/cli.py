"""NightForge command line.

    python -m nightforge.cli research --sample

``--sample`` caps the run at three leads and four research workers, and keeps
the seed top-up and fallback pitch in play, so it produces three complete rows
even with no API keys and a dead search backend.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Sequence

from leadforge.run_context import RunOptions


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nightforge.cli",
        description="NightForge research runs. No send path; every row needs review.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    research = subcommands.add_parser(
        "research", help="Run the research graph end to end."
    )
    research.add_argument(
        "--sample",
        action="store_true",
        help="3 leads, 4 workers, seeds and fallbacks on. Safe without keys.",
    )
    research.add_argument("--max-leads", type=int, default=None)
    research.add_argument("--zip", dest="target_zip", default=None)
    research.add_argument(
        "--budget", type=float, default=None, help="Per-run cap in USD (max 10)."
    )
    research.add_argument(
        "--output-dir", default=None, help="Override the export directory."
    )
    research.add_argument("--quiet", action="store_true")
    return parser


def _run_research(args: argparse.Namespace) -> int:
    # Imported here so `--help` does not pay for LangGraph and LangChain.
    from nightforge.graphs.research import run_research

    options = RunOptions(
        sample_mode=args.sample,
        max_leads=args.max_leads,
        target_zip=args.target_zip,
        budget_usd=args.budget,
    )
    summary = run_research(options, output_directory=args.output_dir)

    print(json.dumps(summary, indent=2, default=str))
    if summary["stopped_early"]:
        print(f"\nStopped early: {summary['stop_reason']}", file=sys.stderr)
    csv_path = summary["export_paths"].get("csv")
    if csv_path:
        print(
            f"\n{summary['leads_pitched']} row(s) -> {csv_path}"
            f" | est. ${summary['estimated_cost_usd']:.4f}"
            " | human review required on every row"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if args.command == "research":
        return _run_research(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
