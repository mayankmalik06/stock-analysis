"""
tests/test_events_score.py

Unit tests for compute_events_score() in app/services/scoring.py.

Tests verify:
1.  Empty list → 0.0
2.  All NO_EVENT → 0.0
3.  Single EARNINGS POSITIVE at confidence 1.0 → >= 80
4.  Single BROKER_RATING POSITIVE at confidence 0.8 → in 75–95 band
5.  Single RISK NEGATIVE at confidence 1.0 → <= 25
6.  Two EARNINGS POSITIVE events give a higher score than one (extra-event bonus)
7.  Confidence multiplier: lower confidence reduces the score
8.  Result is always a float in [0.0, 100.0]
9.  NO_EVENT entries in a mixed list are ignored
10. Multi-type list: best event drives the base score
11. Bonus is capped at _EXTRA_EVENT_CAP
12. MACRO events always score in the 30–50 range regardless of sentiment
13. GENERAL_NEWS events score in 25–45 range

Run with:
    pytest tests/test_events_score.py -v
"""

import pytest
from app.services.scoring import compute_events_score


def make_event(event_type: str, sentiment: str, confidence: float = 1.0) -> dict:
    return {"event_type": event_type, "sentiment": sentiment, "confidence": confidence}


# ── Tests: edge cases ─────────────────────────────────────────────────────────

def test_empty_list_returns_zero():
    assert compute_events_score([]) == 0.0


def test_all_no_event_returns_zero():
    events = [
        make_event("NO_EVENT", "NEUTRAL"),
        make_event("NO_EVENT", "POSITIVE"),
    ]
    assert compute_events_score(events) == 0.0


def test_single_no_event_with_none_type():
    events = [{"event_type": None, "sentiment": "NEUTRAL", "confidence": 0.9}]
    assert compute_events_score(events) == 0.0


# ── Tests: score bands ────────────────────────────────────────────────────────

def test_earnings_positive_full_confidence():
    """EARNINGS + POSITIVE at confidence=1.0 → top of band (100.0)."""
    events = [make_event("EARNINGS", "POSITIVE", confidence=1.0)]
    score = compute_events_score(events)
    assert score == 100.0, f"Expected 100.0, got {score}"


def test_earnings_positive_zero_confidence():
    """EARNINGS + POSITIVE at confidence=0.0 → bottom of band (80.0)."""
    events = [make_event("EARNINGS", "POSITIVE", confidence=0.0)]
    score = compute_events_score(events)
    assert score == 80.0, f"Expected 80.0, got {score}"


def test_broker_rating_positive_partial_confidence():
    """BROKER_RATING + POSITIVE at confidence=0.8 → within [75, 95] band."""
    events = [make_event("BROKER_RATING", "POSITIVE", confidence=0.8)]
    score = compute_events_score(events)
    # band = (75, 95), score = 75 + 20 * 0.8 = 91.0
    assert 75.0 <= score <= 95.0, f"Score {score} outside expected band [75, 95]"


def test_risk_negative_full_confidence():
    """RISK + NEGATIVE at confidence=1.0 → top of band (25.0)."""
    events = [make_event("RISK", "NEGATIVE", confidence=1.0)]
    score = compute_events_score(events)
    assert score == 25.0, f"Expected 25.0, got {score}"


def test_risk_negative_zero_confidence():
    """RISK + NEGATIVE at confidence=0.0 → bottom of band (5.0)."""
    events = [make_event("RISK", "NEGATIVE", confidence=0.0)]
    score = compute_events_score(events)
    assert score == 5.0, f"Expected 5.0, got {score}"


def test_macro_event_any_sentiment_in_range():
    """MACRO events should always score between 30 and 50."""
    for sentiment in ["POSITIVE", "NEGATIVE", "NEUTRAL"]:
        events = [make_event("MACRO", sentiment, confidence=0.9)]
        score = compute_events_score(events)
        assert 30.0 <= score <= 50.0, (
            f"MACRO {sentiment} score {score} outside [30, 50]"
        )


def test_general_news_in_range():
    """GENERAL_NEWS events should score between 25 and 45."""
    for sentiment in ["POSITIVE", "NEUTRAL", "NEGATIVE"]:
        events = [make_event("GENERAL_NEWS", sentiment, confidence=0.8)]
        score = compute_events_score(events)
        assert 25.0 <= score <= 45.0, (
            f"GENERAL_NEWS {sentiment} score {score} outside [25, 45]"
        )


# ── Tests: extra-event bonus ──────────────────────────────────────────────────

def test_two_earnings_positive_higher_than_one():
    """Two EARNINGS POSITIVE events give a higher score than one."""
    one_event = [make_event("EARNINGS", "POSITIVE", confidence=0.9)]
    two_events = [
        make_event("EARNINGS", "POSITIVE", confidence=0.9),
        make_event("EARNINGS", "POSITIVE", confidence=0.8),
    ]
    score_one = compute_events_score(one_event)
    score_two = compute_events_score(two_events)
    assert score_two > score_one, f"Two events ({score_two}) should beat one ({score_one})"


def test_extra_event_bonus_capped():
    """Adding many same-tier events should not exceed 100."""
    events = [make_event("EARNINGS", "POSITIVE", confidence=0.95) for _ in range(20)]
    score = compute_events_score(events)
    assert score <= 100.0, f"Score should never exceed 100.0, got {score}"


def test_different_tier_events_best_drives_score():
    """When multiple event types are present, the best one sets the base score."""
    events = [
        make_event("RISK", "NEGATIVE", confidence=1.0),       # band 5–25
        make_event("EARNINGS", "POSITIVE", confidence=0.95),  # band 80–100
        make_event("GENERAL_NEWS", "NEUTRAL", confidence=0.8), # band 25–45
    ]
    score = compute_events_score(events)
    # Best event is EARNINGS POSITIVE → base ~99, no same-tier extras
    assert score >= 80.0, f"Best event (EARNINGS POSITIVE) should drive score >= 80, got {score}"


# ── Tests: confidence effect ──────────────────────────────────────────────────

def test_lower_confidence_lowers_score():
    """Lower confidence should always give a lower score within the same band."""
    high_conf = compute_events_score([make_event("BROKER_RATING", "POSITIVE", confidence=0.95)])
    low_conf = compute_events_score([make_event("BROKER_RATING", "POSITIVE", confidence=0.3)])
    assert high_conf > low_conf, (
        f"High confidence ({high_conf}) should beat low confidence ({low_conf})"
    )


# ── Tests: NO_EVENT mixed with real events ────────────────────────────────────

def test_no_event_entries_ignored_in_mixed_list():
    """NO_EVENT rows in a mixed list should not affect the outcome."""
    events_without_no_event = [make_event("EARNINGS", "POSITIVE", confidence=0.9)]
    events_with_no_event = [
        make_event("NO_EVENT", "NEUTRAL", confidence=1.0),
        make_event("EARNINGS", "POSITIVE", confidence=0.9),
        make_event("NO_EVENT", "NEUTRAL", confidence=1.0),
    ]
    score_clean = compute_events_score(events_without_no_event)
    score_mixed = compute_events_score(events_with_no_event)
    assert score_clean == score_mixed, (
        f"NO_EVENT rows should be ignored. Clean={score_clean}, Mixed={score_mixed}"
    )


# ── Tests: score bounds ───────────────────────────────────────────────────────

@pytest.mark.parametrize("event_type,sentiment,confidence", [
    ("EARNINGS", "POSITIVE", 1.0),
    ("EARNINGS", "NEGATIVE", 0.0),
    ("RISK", "NEGATIVE", 1.0),
    ("MACRO", "NEUTRAL", 0.5),
    ("FLOW", "POSITIVE", 0.7),
    ("CORPORATE_ACTION", "POSITIVE", 0.9),
    ("BROKER_RATING", "NEGATIVE", 0.3),
    ("GUIDANCE", "NEUTRAL", 0.6),
])
def test_score_always_in_0_to_100(event_type, sentiment, confidence):
    """Every valid event combination must produce a score in [0, 100]."""
    events = [make_event(event_type, sentiment, confidence)]
    score = compute_events_score(events)
    assert 0.0 <= score <= 100.0, (
        f"Score {score} out of range for ({event_type}, {sentiment}, {confidence})"
    )
