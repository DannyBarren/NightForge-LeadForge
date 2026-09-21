"""The scoring rules. All of them, written down, in one place.

No model, no training, no weights fitted to anything. Every band and every
point of score traces to a rule below, and every rule names the signals it used
in ``reasons``. Someone deciding whether to chase a lead can read why it ranked
where it did, and someone reviewing this file can change the policy by editing
a number rather than retraining.

The rules, in the order they are applied:

1. Disqualifiers — already a customer, outside the service area, or a duplicate
   inside 30 days — drop the lead outright.
2. Three or more agreeing signals in 48 hours is hot.
3. Two signals, or one strong signal alongside a weather trigger, is warm.
4. One signal is a log entry.
5. No signals at all is a log entry with nothing behind it.

Qualifying leads route to ``REVIEW``, never straight to ``ROUTE``. A person
decides.
"""

from __future__ import annotations

from dataclasses import dataclass

from nightforge.scoring.schema import Band, LeadFeatures, RouteDecision

HOT_SIGNAL_THRESHOLD = 3
WARM_SIGNAL_THRESHOLD = 2

BAND_BASE_SCORE = {Band.HOT: 0.85, Band.WARM: 0.55, Band.LOG: 0.20}
WEATHER_BONUS = 0.05
STRONG_SIGNAL_BONUS = 0.05
DISQUALIFIED_SCORE = 0.0

# Fewer signals, wider interval. This is honest uncertainty, not a model's.
CONFIDENCE_WIDTH_BY_SIGNAL_COUNT = {0: 0.20, 1: 0.15, 2: 0.10}
NARROW_CONFIDENCE_WIDTH = 0.05


@dataclass(frozen=True)
class Verdict:
    """The rules' output for one lead."""

    score: float
    band: Band
    route_decision: RouteDecision
    reasons: tuple[str, ...]
    confidence_lo: float
    confidence_hi: float


def _describe_signals(features: LeadFeatures) -> str:
    if not features.sources:
        return "no demand signals"
    return f"{features.signal_count_48h} signal(s) in 48h: " + ", ".join(
        features.sources
    )


def _disqualifiers(features: LeadFeatures) -> list[str]:
    reasons: list[str] = []
    if features.already_customer:
        reasons.append("dropped: already a customer")
    if not features.in_service_area:
        reasons.append(f"dropped: zip {features.zip} is outside the service area")
    if features.duplicate_30d:
        reasons.append("dropped: duplicate of a lead seen in the last 30 days")
    return reasons


def _confidence(score: float, signal_count: int) -> tuple[float, float]:
    width = CONFIDENCE_WIDTH_BY_SIGNAL_COUNT.get(
        signal_count, NARROW_CONFIDENCE_WIDTH
    )
    return (round(max(0.0, score - width), 4), round(min(1.0, score + width), 4))


def evaluate(features: LeadFeatures) -> Verdict:
    """Apply the rules. Same features in, same verdict out, always."""
    disqualified = _disqualifiers(features)
    if disqualified:
        return Verdict(
            score=DISQUALIFIED_SCORE,
            band=Band.LOG,
            route_decision=RouteDecision.DROP,
            reasons=tuple(disqualified + [_describe_signals(features)]),
            confidence_lo=0.0,
            confidence_hi=0.0,
        )

    count = features.signal_count_48h
    reasons: list[str] = [_describe_signals(features)]

    if count >= HOT_SIGNAL_THRESHOLD:
        band = Band.HOT
        reasons.append(f"{count} agreeing signals within 48h")
    elif count >= WARM_SIGNAL_THRESHOLD:
        band = Band.WARM
        reasons.append(f"{count} agreeing signals within 48h")
    elif count == 1 and features.strong_signal_count >= 1 and features.weather_trigger:
        band = Band.WARM
        reasons.append("one strong signal alongside a weather trigger")
    elif count >= 1:
        band = Band.LOG
        reasons.append("single signal, logged for context")
    else:
        band = Band.LOG
        reasons.append("no demand signals in the last 48h")

    score = BAND_BASE_SCORE[band]
    if features.weather_trigger:
        score += WEATHER_BONUS
        reasons.append("weather trigger present")
    if features.strong_signal_count >= 1:
        score += STRONG_SIGNAL_BONUS
        reasons.append(
            f"{features.strong_signal_count} signal(s) came from a source where "
            "a homeowner asked for the work"
        )
    score = round(min(1.0, max(0.0, score)), 4)

    route = RouteDecision.DROP if band is Band.LOG else RouteDecision.REVIEW
    if route is RouteDecision.REVIEW:
        reasons.append("routed to human review — nothing is contacted automatically")

    low, high = _confidence(score, count)
    return Verdict(
        score=score,
        band=band,
        route_decision=route,
        reasons=tuple(reasons),
        confidence_lo=low,
        confidence_hi=high,
    )
