"""Tests for the nightforge budget governor.

Covers the four layers: per-run cap, per-day/per-client ceiling, loop
protection, and the usage ledger — plus the rule that no call is ever booked
at zero.
"""

import json
import threading

import pytest

from leadforge.config_loader import AppConfig, GuardrailsConfig, LLMConfig
from leadforge.cost_tracker import CostTracker
from nightforge.governor import (
    EST_DISCOVERY_IN,
    EST_DISCOVERY_OUT,
    EST_PITCH_IN,
    EST_PITCH_OUT,
    EST_RESEARCH_IN,
    EST_RESEARCH_OUT,
    HARD_CEILING_USD,
    INBOUND_BUDGET_USD,
    RESEARCH_BUDGET_USD,
    RESEARCH_STOP_PROJECTED_USD,
    BreakerTripped,
    BudgetPolicy,
    DailyLedger,
    Governor,
    GovernorDeferred,
    GovernorHalt,
    LoopLimits,
    RunKind,
)

# 500k in / 500k out at gpt-4o-mini rates: 0.075 + 0.30.
HALF_MILLION_EACH_USD = 0.375


def _config(budget: float = 8.0, stop: float = 7.25) -> AppConfig:
    return AppConfig(
        guardrails=GuardrailsConfig(budget_usd=budget, stop_projected_usd=stop),
        llm=LLMConfig(),
        pricing_usd_per_million={"gpt-4o-mini": {"input": 0.15, "output": 0.60}},
    )


# ---- layer 1: per-run cap ---------------------------------------------


def test_per_run_cap_raises_halt_and_records_stop_reason():
    gov = Governor(_config(budget=0.05, stop=0.05))
    gov.record("LeadDiscoveryAgent", "discovery", 500_000, 500_000)

    with pytest.raises(GovernorHalt) as exc:
        gov.check_budget_before_phase("research", EST_RESEARCH_IN, EST_RESEARCH_OUT)

    assert exc.value.stop_reason
    assert "research" in exc.value.stop_reason
    assert gov.stopped
    assert gov.stop_reason == exc.value.stop_reason


def test_halt_is_a_clean_stop_not_a_crash():
    """A halted run keeps its ledger, so partial work can still be exported."""
    gov = Governor(_config(budget=0.05, stop=0.05))
    gov.record("LeadDiscoveryAgent", "discovery", 500_000, 500_000)
    with pytest.raises(GovernorHalt):
        gov.check_budget_before_phase("pitch", EST_PITCH_IN, EST_PITCH_OUT)

    summary = gov.summary()
    assert summary["stopped_early"] is True
    assert summary["stop_reason"]
    assert len(summary["records"]) == 1
    assert summary["estimated_cost_usd"] == pytest.approx(HALF_MILLION_EACH_USD)


def test_projected_stop_fires_before_the_hard_budget():
    """The early stop is what catches a run: 500k/100k costs exactly $0.135."""
    gov = Governor(_config(budget=8.0, stop=0.136))
    gov.record("ResearchAgent", "research", 500_000, 100_000)

    # Spend so far is under both thresholds; only the projection crosses.
    assert gov.total_cost_usd() == pytest.approx(0.135)
    decision = gov.can_afford(EST_RESEARCH_IN, EST_RESEARCH_OUT)
    assert decision.allowed is False
    assert "Projected" in decision.reason


def test_can_afford_allows_a_normal_call():
    gov = Governor(_config())
    assert gov.can_afford(EST_DISCOVERY_IN, EST_DISCOVERY_OUT).allowed is True


def test_should_stop_latches_the_run():
    gov = Governor(_config(budget=0.05, stop=0.05))
    gov.record("ResearchAgent", "research", 500_000, 500_000)

    assert gov.should_stop().allowed is False
    assert gov.stopped
    # Latched: a later affordable call is still refused.
    assert gov.can_afford(1, 1).allowed is False


def test_remaining_pipeline_stops_while_the_run_can_still_finish():
    gov = Governor(_config(budget=1.0, stop=1.0))
    with pytest.raises(GovernorHalt):
        gov.check_remaining_pipeline(
            "research-queue", leads_left_research=400, leads_left_pitch=400
        )
    assert gov.total_cost_usd() == 0.0


def test_research_and_inbound_policies_use_the_documented_defaults():
    research = BudgetPolicy.for_research()
    assert research.kind is RunKind.RESEARCH
    assert research.budget_usd == RESEARCH_BUDGET_USD
    assert research.stop_projected_usd == RESEARCH_STOP_PROJECTED_USD

    inbound = BudgetPolicy.for_inbound()
    assert inbound.kind is RunKind.INBOUND
    assert inbound.budget_usd == INBOUND_BUDGET_USD


def test_policy_clamps_to_the_hard_ceiling():
    assert BudgetPolicy.for_inbound(999.0).budget_usd == HARD_CEILING_USD
    policy = BudgetPolicy(RunKind.RESEARCH, budget_usd=4.0, stop_projected_usd=9.0)
    assert policy.stop_projected_usd == 4.0


# ---- layer 2: per-day, per-client ceiling ------------------------------


def test_daily_cap_defers_a_second_run():
    ledger = DailyLedger(daily_cap_usd=0.50)
    cfg = _config()

    first = Governor(
        cfg, ledger=ledger, client_id="acme", policy=BudgetPolicy.for_inbound(0.40)
    )
    first.require_admission()
    first.record("InboundAgent", "inbound", 500_000, 500_000)

    second = Governor(
        cfg, ledger=ledger, client_id="acme", policy=BudgetPolicy.for_inbound(0.40)
    )
    with pytest.raises(GovernorDeferred) as exc:
        second.require_admission()
    assert "daily ceiling" in str(exc.value).lower()

    # Deferred, not halted: nothing was spent on the second run.
    assert second.total_cost_usd() == 0.0
    assert second.stopped is False


def test_daily_cap_is_scoped_per_client():
    ledger = DailyLedger(daily_cap_usd=0.50)
    cfg = _config()

    Governor(cfg, ledger=ledger, client_id="acme").record(
        "InboundAgent", "inbound", 500_000, 500_000
    )

    other = Governor(
        cfg, ledger=ledger, client_id="other", policy=BudgetPolicy.for_inbound(0.40)
    )
    assert other.admit().allowed is True
    assert ledger.spent("acme") == pytest.approx(HALF_MILLION_EACH_USD)
    assert ledger.spent("other") == 0.0


def test_daily_ledger_persists_across_processes(tmp_path):
    path = tmp_path / "daily.json"
    DailyLedger(daily_cap_usd=0.50, path=path).add("acme", 0.45)

    reloaded = DailyLedger(daily_cap_usd=0.50, path=path)
    assert reloaded.spent("acme") == pytest.approx(0.45)
    assert reloaded.admit("acme", projected_usd=0.40).allowed is False


def test_governor_without_a_ledger_admits():
    assert Governor(_config()).admit().allowed is True


# ---- layer 3: loop protection ------------------------------------------


def test_forced_retry_loop_trips_the_breaker_under_the_cap():
    gov = Governor(_config())
    delays = []

    with pytest.raises(BreakerTripped) as exc:
        for _ in range(10):
            delays.append(gov.note_retryable_error(429, tool="web_search"))

    # Bounded backoff, then fail closed — not an unbounded retry.
    assert delays == [2.0, 4.0]
    assert "429" in str(exc.value)
    # The breaker fired on loop shape, with the budget still untouched.
    assert gov.total_cost_usd() == 0.0
    assert gov.stopped
    assert gov.can_afford(1, 1).allowed is False


def test_backoff_is_clamped_to_the_ceiling():
    gov = Governor(_config(), limits=LoopLimits(max_retry_attempts=99))
    assert gov.backoff_delay(1) == 2.0
    assert gov.backoff_delay(2) == 4.0
    assert gov.backoff_delay(10) == gov.limits.retry_max_delay_sec


def test_non_retryable_status_fails_closed_immediately():
    gov = Governor(_config())
    with pytest.raises(BreakerTripped) as exc:
        gov.note_retryable_error(403, tool="web_search")
    assert "non-retryable" in str(exc.value)


def test_server_errors_are_retryable_and_client_errors_are_not():
    gov = Governor(_config())
    assert gov.is_retryable(429) is True
    assert gov.is_retryable(503) is True
    assert gov.is_retryable(404) is False


def test_repeated_identical_tool_call_trips_the_breaker_under_the_cap():
    gov = Governor(_config())
    args = {"query": "hvac phoenix"}
    for _ in range(gov.limits.max_repeat_tool_calls):
        gov.note_tool_call("web_search", args)

    with pytest.raises(BreakerTripped):
        gov.note_tool_call("web_search", args)

    assert gov.total_cost_usd() == 0.0
    assert "Repeated tool call" in gov.stop_reason


def test_distinct_tool_calls_do_not_trip_the_repeat_breaker():
    gov = Governor(_config())
    for i in range(10):
        gov.note_tool_call("web_search", {"query": f"query {i}"})
    assert gov.loop_state()["tool_calls"] == 10


def test_iteration_and_tool_call_limits_trip():
    gov = Governor(_config(), limits=LoopLimits(max_iterations=2, max_tool_calls=2))
    gov.note_iteration()
    gov.note_iteration()
    with pytest.raises(BreakerTripped):
        gov.note_iteration()

    gov = Governor(_config(), limits=LoopLimits(max_tool_calls=2))
    gov.note_tool_call("a")
    gov.note_tool_call("b")
    with pytest.raises(BreakerTripped):
        gov.note_tool_call("c")


def test_a_successful_call_clears_its_retry_count():
    gov = Governor(_config())
    gov.note_retryable_error(429, tool="web_search")
    gov.reset_retries("web_search")
    assert gov.note_retryable_error(429, tool="web_search") == 2.0


# ---- layers 4 and 5: ledger, estimation, thread safety -----------------


def test_no_call_is_ever_recorded_at_zero():
    gov = Governor(_config())
    usd = gov.record("ResearchAgent", "research", 0, 0)

    assert usd > 0
    record = gov.records[0]
    assert record.estimated is True
    assert record.input_tokens == 200
    assert record.output_tokens == 200


def test_missing_provider_usage_falls_back_to_the_char_heuristic():
    gov = Governor(_config())
    gov.record_from_usage("PitchAgent", "pitch", None, fallback_text="x" * 4_000)

    record = gov.records[0]
    assert record.estimated is True
    assert record.input_tokens == 1_000


def test_reported_usage_is_not_marked_estimated():
    gov = Governor(_config())
    gov.record_from_usage(
        "PitchAgent", "pitch", {"prompt_tokens": 1_800, "completion_tokens": 1_200}
    )
    record = gov.records[0]
    assert record.estimated is False
    assert (record.input_tokens, record.output_tokens) == (EST_PITCH_IN, EST_PITCH_OUT)


def test_usage_object_attributes_are_read_like_the_prototype():
    class Usage:
        prompt_tokens = 2_500
        completion_tokens = 2_000

    gov = Governor(_config())
    gov.record_from_usage("ResearchAgent", "research", Usage())
    record = gov.records[0]
    assert (record.input_tokens, record.output_tokens) == (
        EST_RESEARCH_IN,
        EST_RESEARCH_OUT,
    )


def test_usage_log_stays_compatible_with_the_prototype_format(tmp_path):
    cfg = _config()
    gov = Governor(cfg)
    gov.record("LeadDiscoveryAgent", "discovery", EST_DISCOVERY_IN, EST_DISCOVERY_OUT)

    tracker = CostTracker(config=cfg, model="gpt-4o-mini")
    tracker.record("LeadDiscoveryAgent", "discovery", EST_DISCOVERY_IN, EST_DISCOVERY_OUT)

    payload = json.loads(
        gov.write_log("20260101T000000Z", out_dir=tmp_path).read_text(encoding="utf-8")
    )
    assert set(tracker.summary()) <= set(payload)
    assert set(tracker.summary()["records"][0]) <= set(payload["records"][0])
    assert payload["run_id"] == "20260101T000000Z"
    assert payload["estimated_cost_usd"] == pytest.approx(
        round(tracker.total_cost_usd(), 4)
    )


def test_cumulative_total_is_monotonic():
    gov = Governor(_config())
    for _ in range(5):
        gov.record("ResearchAgent", "research", EST_RESEARCH_IN, EST_RESEARCH_OUT)
    totals = [r.cumulative_usd for r in gov.records]
    assert totals == sorted(totals)
    assert totals[-1] == pytest.approx(gov.total_cost_usd())


def test_ledger_is_thread_safe_under_parallel_research():
    gov = Governor(_config())

    def work():
        for _ in range(25):
            gov.record("ResearchAgent", "research", 100, 100)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(gov.records) == 200
    assert gov.total_cost_usd() == pytest.approx(
        sum(r.estimated_usd for r in gov.records)
    )
    assert gov.summary()["total_input_tokens"] == 20_000


def test_unpriced_model_falls_back_above_the_cheap_rate():
    gov = Governor(_config(), model="some-unlisted-model")
    assert gov.pricing()["input"] > 0.15


# ---- the prototype keeps its own halt exception -------------------------


def test_leadforge_guardrail_violation_is_independent_of_governor_halt():
    from leadforge.guardrails import GuardrailViolation

    assert not issubclass(GuardrailViolation, GovernorHalt)
    assert not issubclass(GovernorHalt, GuardrailViolation)
