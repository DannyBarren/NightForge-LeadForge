"""Offline NightForge eval harness.

Reads ``evals/cases.json``, runs the real scoring / governor / research / API
code against fixtures and sample mode, and prints a pass/fail report.

Paid APIs are never called. Provider keys are stripped from the process
environment before any case runs. Re-run with::

    python -m evals.harness
    python -m evals.harness --json evals/report.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASES_PATH = ROOT / "evals" / "cases.json"
PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_API_KEY",
)


@dataclass
class CaseResult:
    case_id: str
    suite: str
    description: str
    passed: bool
    detail: str = ""
    elapsed_s: float = 0.0


@dataclass
class Report:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for item in self.results if item.passed)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if not item.passed)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and bool(self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "passed": self.passed,
            "failed": self.failed,
            "total": len(self.results),
            "results": [
                {
                    "id": item.case_id,
                    "suite": item.suite,
                    "passed": item.passed,
                    "detail": item.detail,
                    "elapsed_s": round(item.elapsed_s, 4),
                    "description": item.description,
                }
                for item in self.results
            ],
        }


def _strip_provider_keys() -> None:
    for key in PROVIDER_KEYS:
        os.environ.pop(key, None)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _expect_eq(actual: Any, expected: Any, field: str) -> None:
    if actual != expected:
        raise AssertionError(f"{field}: expected {expected!r}, got {actual!r}")


def _run_recorded_sample_scores(case: dict[str, Any]) -> str:
    from nightforge.scoring.pipeline import score_events
    from nightforge.scoring.sample_events import (
        SAMPLE_SERVICE_AREA_ZIPS,
        SAMPLE_ZONE_MAP,
        sample_signal_events,
    )

    fixture = _load_json(ROOT / case["fixture"])
    now = datetime.fromisoformat(fixture["now"])
    leads = score_events(
        sample_signal_events(now=now),
        now=now,
        service_area_zips=SAMPLE_SERVICE_AREA_ZIPS,
        zone_map=SAMPLE_ZONE_MAP,
    )
    by_id = {lead.lead_id: lead for lead in leads}
    _expect_eq(len(leads), len(fixture["leads"]), "lead_count")
    for expected in fixture["leads"]:
        lead = by_id.get(expected["lead_id"])
        if lead is None:
            raise AssertionError(f"missing lead {expected['lead_id']}")
        _expect_eq(lead.band.value, expected["band"], f"{lead.lead_id}.band")
        _expect_eq(lead.score, expected["score"], f"{lead.lead_id}.score")
        _expect_eq(
            lead.route_decision.value,
            expected["route_decision"],
            f"{lead.lead_id}.route",
        )
        _expect_eq(
            lead.suggested_zone,
            expected["suggested_zone"],
            f"{lead.lead_id}.zone",
        )
        _expect_eq(
            lead.suggested_unit,
            expected["suggested_unit"],
            f"{lead.lead_id}.unit",
        )
        _expect_eq(
            lead.human_review_required,
            expected["human_review_required"],
            f"{lead.lead_id}.human_review",
        )
    bands = [lead.band.value for lead in leads]
    return f"{len(leads)} leads labeled {bands}"


def _run_heuristic_features(case: dict[str, Any]) -> str:
    from nightforge.scoring.heuristic import evaluate
    from nightforge.scoring.schema import LeadFeatures

    verdict = evaluate(LeadFeatures(**case["features"]))
    expect = case["expect"]
    if "band" in expect:
        _expect_eq(verdict.band.value, expect["band"], "band")
    if "route_decision" in expect:
        _expect_eq(verdict.route_decision.value, expect["route_decision"], "route")
    if "score" in expect:
        _expect_eq(verdict.score, expect["score"], "score")
    if "min_score" in expect and verdict.score < expect["min_score"]:
        raise AssertionError(
            f"score {verdict.score} is below min_score {expect['min_score']}"
        )
    if verdict.route_decision.value == "route":
        raise AssertionError("heuristic emitted ROUTE; nothing auto-contacts")
    return f"band={verdict.band.value} route={verdict.route_decision.value} score={verdict.score}"


def _governor(case: dict[str, Any]):
    from leadforge.config_loader import AppConfig, GuardrailsConfig, LLMConfig
    from nightforge.governor import Governor

    return Governor(
        AppConfig(
            guardrails=GuardrailsConfig(
                budget_usd=case["budget_usd"],
                stop_projected_usd=case["stop_projected_usd"],
            ),
            llm=LLMConfig(),
            pricing_usd_per_million={
                "gpt-4o-mini": {"input": 0.15, "output": 0.60}
            },
        )
    )


def _run_governor_halt(case: dict[str, Any]) -> str:
    from nightforge.governor import EST_RESEARCH_IN, EST_RESEARCH_OUT

    gov = _governor(case)
    tokens_in, tokens_out = case["record_tokens"]
    gov.record("LeadDiscoveryAgent", "discovery", tokens_in, tokens_out)
    decision = gov.can_afford(EST_RESEARCH_IN, EST_RESEARCH_OUT)
    _expect_eq(decision.allowed, case["expect"]["allowed"], "allowed")
    if not decision.allowed and not decision.reason:
        raise AssertionError("refused call has an empty reason")
    return f"allowed={decision.allowed} reason={decision.reason or 'ok'}"


def _run_governor_allow(case: dict[str, Any]) -> str:
    from nightforge.governor import EST_DISCOVERY_IN, EST_DISCOVERY_OUT

    gov = _governor(case)
    decision = gov.can_afford(EST_DISCOVERY_IN, EST_DISCOVERY_OUT)
    _expect_eq(decision.allowed, case["expect"]["allowed"], "allowed")
    return f"allowed={decision.allowed}"


def _run_research_sample(case: dict[str, Any]) -> str:
    from leadforge.run_context import RunOptions
    from nightforge.graphs.research import run_research

    expect = case["expect"]
    with tempfile.TemporaryDirectory(prefix="nightforge-eval-") as tmp:
        summary = run_research(
            RunOptions(sample_mode=True),
            output_directory=tmp,
        )
        return _check_research_sample(summary, expect)


def _check_research_sample(summary: dict[str, Any], expect: dict[str, Any]) -> str:
    _expect_eq(summary["leads_pitched"], expect["leads_pitched"], "leads_pitched")
    _expect_eq(
        summary["estimated_cost_usd"],
        expect["estimated_cost_usd"],
        "estimated_cost_usd",
    )
    csv_path = Path(summary["export_paths"]["csv"])

    with csv_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    reviews = {row["Human Review"] for row in rows}
    _expect_eq(reviews, {expect["human_review"]}, "Human Review")
    if any(not row["Draft Email"].strip() for row in rows):
        raise AssertionError("a pitched row is missing a draft email")
    return (
        f"{summary['leads_pitched']} rows, cost ${summary['estimated_cost_usd']:.4f}, "
        f"review={expect['human_review']}"
    )


def _run_api_no_send(case: dict[str, Any]) -> str:
    from nightforge.api.app import app

    forbidden = tuple(case["forbidden_tokens"])
    paths = []
    for route in app.routes:
        path = getattr(route, "path", "") or ""
        paths.append(path)
        lowered = path.lower()
        hits = [token for token in forbidden if token in lowered]
        if hits:
            raise AssertionError(f"forbidden token {hits} in route {path}")
    return f"{len(paths)} routes, none match {list(forbidden)}"


def _run_webhook_fail_closed(case: dict[str, Any]) -> str:
    from nightforge.adapters.jobber import JobberAdapter, WEBHOOK_SECRET_ENV

    os.environ.pop(WEBHOOK_SECRET_ENV, None)
    ok = JobberAdapter().verify_webhook(
        case["payload"].encode("utf-8"),
        {},
    )
    if ok:
        raise AssertionError("unsigned, secret-less webhook was accepted")
    return "rejected"


def _run_human_review_forced(_case: dict[str, Any]) -> str:
    from nightforge.scoring.schema import Band, LeadFeatures, RouteDecision, ScoredLead

    lead = ScoredLead(
        lead_id="eval-forced-review",
        features=LeadFeatures(),
        score=0.9,
        band=Band.HOT,
        confidence_lo=0.8,
        confidence_hi=1.0,
        route_decision=RouteDecision.REVIEW,
        human_review_required=False,
    )
    if lead.human_review_required is not True:
        raise AssertionError("human_review_required accepted False")
    return "pinned True"


_RUNNERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "recorded_sample_scores": _run_recorded_sample_scores,
    "heuristic_features": _run_heuristic_features,
    "governor_halt": _run_governor_halt,
    "governor_allow": _run_governor_allow,
    "research_sample": _run_research_sample,
    "api_no_send": _run_api_no_send,
    "webhook_fail_closed": _run_webhook_fail_closed,
    "human_review_forced": _run_human_review_forced,
}


def run_case(case: dict[str, Any]) -> CaseResult:
    runner = _RUNNERS.get(case["kind"])
    started = time.perf_counter()
    if runner is None:
        return CaseResult(
            case_id=case["id"],
            suite=case.get("suite", "unknown"),
            description=case.get("description", ""),
            passed=False,
            detail=f"unknown kind {case['kind']!r}",
            elapsed_s=0.0,
        )
    try:
        detail = runner(case)
        return CaseResult(
            case_id=case["id"],
            suite=case.get("suite", "unknown"),
            description=case.get("description", ""),
            passed=True,
            detail=detail,
            elapsed_s=time.perf_counter() - started,
        )
    except Exception as exc:  # noqa: BLE001 — report any case failure
        return CaseResult(
            case_id=case["id"],
            suite=case.get("suite", "unknown"),
            description=case.get("description", ""),
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
            elapsed_s=time.perf_counter() - started,
        )


def run_suite(cases_path: Path = CASES_PATH) -> Report:
    _strip_provider_keys()
    payload = _load_json(cases_path)
    report = Report()
    for case in payload["cases"]:
        report.results.append(run_case(case))
    return report


def render(report: Report) -> str:
    width = max((len(item.case_id) for item in report.results), default=8)
    lines = [
        "NightForge offline eval",
        "=======================",
        (
            f"{len(report.results)} cases  |  {report.passed} passed  |  "
            f"{report.failed} failed"
        ),
        "",
    ]
    for item in report.results:
        mark = "PASS" if item.passed else "FAIL"
        lines.append(
            f"{mark}  {item.case_id:<{width}}  {item.suite:<10}  {item.detail}"
        )
    lines.append("")
    lines.append("Result: PASS" if report.ok else "Result: FAIL")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline NightForge evals.")
    parser.add_argument(
        "--cases",
        type=Path,
        default=CASES_PATH,
        help="Path to the labeled case file.",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        type=Path,
        default=None,
        help="Optional path to write a JSON report.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = run_suite(args.cases)
    text = render(report)
    if not args.quiet:
        print(text)
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(
            json.dumps(report.to_dict(), indent=2), encoding="utf-8"
        )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
