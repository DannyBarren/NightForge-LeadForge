"""The labeled eval suite is part of CI. It must stay offline and green."""

from evals.harness import main, run_suite


def test_offline_eval_harness_passes():
    report = run_suite()
    assert report.ok, [item.detail for item in report.results if not item.passed]


def test_eval_cli_exits_zero():
    assert main(["--quiet"]) == 0
