"""NightForge command line.

    python -m nightforge.cli research --sample
    python -m nightforge.cli research --dry-run
    python -m nightforge.cli score --sample
    python -m nightforge.cli serve

``--sample`` caps the run at three leads and four research workers, and keeps
the seed top-up and fallback pitch in play, so it produces three complete rows
even with no API keys and a dead search backend.

``--dry-run`` validates keys, config, and the governor's opening budget check
without making a single LLM or search call.
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
    research.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config, keys, and the budget gate. No LLM, no spend.",
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

    score = subcommands.add_parser(
        "score", help="Score demand signals into ranked leads."
    )
    score.add_argument(
        "--sample",
        action="store_true",
        help="Score the built-in demo events. Offline, no keys, no LLM.",
    )
    score.add_argument(
        "--output-dir", default=None, help="Override the artifact directory."
    )
    score.add_argument("--quiet", action="store_true")

    serve = subcommands.add_parser("serve", help="Run the FastAPI app under uvicorn.")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument(
        "--reload", action=argparse.BooleanOptionalAction, default=True
    )
    serve.add_argument("--quiet", action="store_true")
    return parser


def _options_from(args: argparse.Namespace) -> RunOptions:
    return RunOptions(
        sample_mode=args.sample,
        max_leads=args.max_leads,
        target_zip=args.target_zip,
        budget_usd=args.budget,
    )


def _research_dry_run(args: argparse.Namespace) -> int:
    """Preflight plus the governor's first budget check. Nothing is called."""
    from leadforge.config_loader import get_config, resolve_industries, resolve_location
    from leadforge.preflight import run_preflight
    from nightforge.governor import (
        EST_DISCOVERY_IN,
        EST_DISCOVERY_OUT,
        Governor,
    )
    from nightforge.graphs.research import default_loop_limits

    config = _options_from(args).apply_to_config(get_config())
    guardrails = config.guardrails

    print("=== NightForge dry run (config only) ===")
    print(f"Location: {resolve_location(config)}")
    print(f"Industries: {resolve_industries(config)}")
    print(f"Model: {config.llm.provider}/{config.llm.model}")
    print(f"Max leads: {guardrails.max_leads_processed}")
    print(
        f"Budget: ${guardrails.budget_usd} "
        f"(early stop @ ${guardrails.stop_projected_usd})"
    )
    print(f"Parallel research: {guardrails.max_parallel_research}")

    print("=== Preflight ===")
    preflight = run_preflight(config)
    for warning in preflight.warnings:
        print(f"  WARN:  {warning}")
    for error in preflight.errors:
        print(f"  ERROR: {error}")
    print(f"  Status: {'OK' if preflight.ok else 'FAILED'}")

    print("=== Governor ===")
    governor = Governor.for_research(
        config, limits=default_loop_limits(guardrails.max_leads_processed)
    )
    decision = governor.can_afford(EST_DISCOVERY_IN, EST_DISCOVERY_OUT)
    limits = governor.limits
    print(f"  Discovery gate: {'OK' if decision.allowed else decision.reason}")
    print(
        f"  Loop limits: {limits.max_iterations} iterations, "
        f"{limits.max_tool_calls} tool calls, "
        f"{limits.max_repeat_tool_calls} repeats, "
        f"{limits.max_retry_attempts} retries"
    )
    print("  Human review: required on every row")
    print("  Send path: none")

    return 0 if preflight.ok and decision.allowed else 1


def _run_research(args: argparse.Namespace) -> int:
    # Imported here so `--help` does not pay for LangGraph and LangChain.
    from nightforge.graphs.research import run_research

    summary = run_research(_options_from(args), output_directory=args.output_dir)

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


def _score(args: argparse.Namespace) -> int:
    """Rank the demo signals. No network, no keys, no model."""
    from nightforge.scoring.pipeline import run_sample

    if not args.sample:
        print(
            "score currently supports --sample only; there is no live signal "
            "source yet.",
            file=sys.stderr,
        )
        return 2

    result = run_sample(output_directory=args.output_dir)

    print(f"=== NightForge scored leads ({len(result.leads)}) ===")
    for lead in result.leads:
        print(
            f"{lead.band.value:<5} {lead.score:>5.2f}  {lead.lead_id}  "
            f"zone={lead.suggested_zone}"
        )
        for reason in lead.reasons:
            print(f"        - {reason}")
    counts = result.band_counts()
    print(
        f"\nBands: hot={counts['hot']} warm={counts['warm']} log={counts['log']} "
        "(warm is kept visible on purpose)"
    )
    print(f"JSON: {result.artifact_path}")
    print("Human review: required on every lead")
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from nightforge.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "nightforge.api.app:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
        log_level="warning" if args.quiet else settings.log_level,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if args.command == "research":
        return _research_dry_run(args) if args.dry_run else _run_research(args)
    if args.command == "score":
        return _score(args)
    if args.command == "serve":
        return _serve(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
