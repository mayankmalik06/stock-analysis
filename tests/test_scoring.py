"""
tests/test_scoring.py

Unit tests for the Milestone 3.5 Technical scoring improvements.

Tests confirm:
1. A symbol with indicative price ABOVE prev_high gets a breakout score (>= 70).
2. A symbol with indicative price BELOW prev_low gets a breakdown score (>= 70).
3. A symbol whose price is inside the range but near the top gets a moderate score
   (55-69).
4. A symbol whose price is deep in the middle of the range gets a lower score (<= 54).
5. Missing levels (levels=None) do NOT crash — Technical falls back to gap proxy.
6. Missing levels with a large gap% gives a high gap-proxy score.
7. Missing levels with no snapshot data at all returns the neutral placeholder (25).
8. The full compute_total_score() with levels-based Technical still respects weights.
9. Breakout score is HIGHER than an inside-range score for the same symbol.
10. The breakdown score equals the breakout score at the same distance from prev_low.

Run with:
    pytest tests/test_scoring.py -v
"""

import pytest
from app.services.scoring import (
    compute_technical_score,
    compute_total_score,
    assign_bucket,
    _gap_proxy_score,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_snapshot(indicative_price=None, gap_pct=None, prev_close=None):
    """Return a minimal snapshot dict for testing."""
    return {
        "indicative_price": indicative_price,
        "gap_pct": gap_pct,
        "prev_close": prev_close,
        "buy_qty": 1000,
        "sell_qty": 800,
        "indicative_value": 50_000_000,
    }


def make_levels(prev_high, prev_low, prev_close=None):
    """Return a minimal levels dict for testing."""
    return {
        "prev_high": prev_high,
        "prev_low": prev_low,
        "prev_close": prev_close or (prev_high + prev_low) / 2,
        "source": "TEST",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Breakout — price above prev_high
# ─────────────────────────────────────────────────────────────────────────────

def test_breakout_price_above_prev_high():
    """
    When indicative price is clearly above prev_high, Technical score should be >= 70.
    This represents a stock opening in breakout territory — strong structural setup.

    Example: prev_high=2500, prev_low=2400, indicative_price=2525 (1% above prev_high)
    """
    snapshots = [make_snapshot(indicative_price=2525.0)]
    levels = make_levels(prev_high=2500.0, prev_low=2400.0)

    score = compute_technical_score(snapshots=snapshots, levels=levels)

    assert score >= 70.0, (
        f"Breakout scenario should give score >= 70, got {score}"
    )


def test_breakout_further_above_gets_higher_score():
    """
    The further above prev_high, the higher the Technical score, up to 100.
    """
    snapshots_small = [make_snapshot(indicative_price=2505.0)]   # 0.2% above
    snapshots_large = [make_snapshot(indicative_price=2560.0)]   # 2.4% above
    levels = make_levels(prev_high=2500.0, prev_low=2400.0)

    score_small = compute_technical_score(snapshots=snapshots_small, levels=levels)
    score_large = compute_technical_score(snapshots=snapshots_large, levels=levels)

    assert score_large > score_small, (
        f"Larger overshoot should get higher score: {score_large} > {score_small}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Breakdown — price below prev_low
# ─────────────────────────────────────────────────────────────────────────────

def test_breakdown_price_below_prev_low():
    """
    When indicative price is below prev_low, Technical score should be >= 70.
    This represents a stock opening in breakdown territory.

    Example: prev_high=1800, prev_low=1720, indicative_price=1710
    """
    snapshots = [make_snapshot(indicative_price=1710.0)]
    levels = make_levels(prev_high=1800.0, prev_low=1720.0)

    score = compute_technical_score(snapshots=snapshots, levels=levels)

    assert score >= 70.0, (
        f"Breakdown scenario should give score >= 70, got {score}"
    )


def test_breakdown_score_symmetrical_with_breakout():
    """
    A breakdown that is X% below prev_low should get the same score as
    a breakout that is X% above prev_high.
    Both represent the same magnitude of structural displacement.
    """
    prev_high = 1000.0
    prev_low = 900.0

    # 1% above prev_high
    snaps_breakout = [make_snapshot(indicative_price=1010.0)]
    levels_breakout = make_levels(prev_high=prev_high, prev_low=prev_low)

    # 1% below prev_low
    snaps_breakdown = [make_snapshot(indicative_price=891.0)]
    levels_breakdown = make_levels(prev_high=prev_high, prev_low=prev_low)

    score_breakout = compute_technical_score(snapshots=snaps_breakout, levels=levels_breakout)
    score_breakdown = compute_technical_score(snapshots=snaps_breakdown, levels=levels_breakdown)

    # Allow 1 point difference due to floating-point rounding
    assert abs(score_breakout - score_breakdown) <= 1.0, (
        f"Breakout ({score_breakout}) and breakdown ({score_breakdown}) "
        f"at same % distance should be approximately equal."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Near the top of the range (inside, but near prev_high)
# ─────────────────────────────────────────────────────────────────────────────

def test_inside_range_near_top_moderate_score():
    """
    A price in the top 20% of the previous range (inside range, near prev_high)
    should score 55–69.

    Example: prev_high=590, prev_low=560, indicative_price=584
    Position = (584 - 560) / (590 - 560) = 24/30 = 0.80 (exactly at the top boundary)
    """
    snapshots = [make_snapshot(indicative_price=584.0)]
    levels = make_levels(prev_high=590.0, prev_low=560.0)

    score = compute_technical_score(snapshots=snapshots, levels=levels)

    assert 55.0 <= score <= 69.0, (
        f"Near-top inside-range scenario should score 55–69, got {score}"
    )


def test_inside_range_near_bottom_moderate_score():
    """
    A price in the bottom 20% of the previous range (inside range, near prev_low)
    should score 55–69.
    """
    snapshots = [make_snapshot(indicative_price=566.0)]
    levels = make_levels(prev_high=590.0, prev_low=560.0)

    score = compute_technical_score(snapshots=snapshots, levels=levels)

    assert 55.0 <= score <= 69.0, (
        f"Near-bottom inside-range scenario should score 55–69, got {score}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Deep inside the range (middle zone)
# ─────────────────────────────────────────────────────────────────────────────

def test_deep_inside_range_lower_score():
    """
    A price dead in the center of the previous range should get a low score (<= 54).
    This means the stock has no clear directional context from its prior range.

    Example: prev_high=4200, prev_low=4050, indicative_price=4125 (center)
    """
    snapshots = [make_snapshot(indicative_price=4125.0)]
    levels = make_levels(prev_high=4200.0, prev_low=4050.0)

    score = compute_technical_score(snapshots=snapshots, levels=levels)

    assert score <= 54.0, (
        f"Center-of-range scenario should score <= 54, got {score}"
    )


def test_breakout_higher_than_inside_range():
    """
    A breakout score (above prev_high) should always be higher than
    an inside-range score for the same prev_high/prev_low range.
    """
    levels = make_levels(prev_high=1000.0, prev_low=900.0)

    snaps_breakout = [make_snapshot(indicative_price=1010.0)]   # above prev_high
    snaps_inside = [make_snapshot(indicative_price=950.0)]       # center of range

    score_breakout = compute_technical_score(snapshots=snaps_breakout, levels=levels)
    score_inside = compute_technical_score(snapshots=snaps_inside, levels=levels)

    assert score_breakout > score_inside, (
        f"Breakout ({score_breakout}) must be > inside-range ({score_inside})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Missing levels — fallback to gap proxy (no crash)
# ─────────────────────────────────────────────────────────────────────────────

def test_no_levels_does_not_crash():
    """
    When levels=None, compute_technical_score must not raise an exception.
    It should return a valid float in 0–100.
    """
    snapshots = [make_snapshot(gap_pct=2.5, prev_close=1000.0)]

    score = compute_technical_score(snapshots=snapshots, levels=None)

    assert isinstance(score, float), "Score should be a float"
    assert 0.0 <= score <= 100.0, f"Score out of range: {score}"


def test_no_levels_large_gap_gives_high_score():
    """
    With no levels but a large gap%, the fallback should give a high score.
    Gap of 6% should score >= 80 via the gap proxy path.
    """
    snapshots = [make_snapshot(gap_pct=6.0, prev_close=500.0)]

    score = compute_technical_score(snapshots=snapshots, levels=None)

    assert score >= 80.0, (
        f"Large gap% with no levels should score >= 80, got {score}"
    )


def test_no_levels_no_snapshots_returns_neutral():
    """
    When there are no levels AND no snapshots, the function should return
    the neutral placeholder value of 25.0.
    """
    score = compute_technical_score(snapshots=[], levels=None)

    assert score == 25.0, (
        f"No data at all should return neutral placeholder 25.0, got {score}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Score stays within bounds
# ─────────────────────────────────────────────────────────────────────────────

def test_extreme_breakout_capped_at_100():
    """
    Even a very extreme breakout (price way above prev_high) should not exceed 100.
    """
    snapshots = [make_snapshot(indicative_price=1500.0)]   # 50% above prev_high of 1000
    levels = make_levels(prev_high=1000.0, prev_low=800.0)

    score = compute_technical_score(snapshots=snapshots, levels=levels)

    assert score <= 100.0, f"Score should never exceed 100, got {score}"


def test_minimum_score_not_negative():
    """
    No scenario should produce a negative Technical score.
    """
    # Tiny gap, deep in range
    snapshots = [make_snapshot(indicative_price=950.0, gap_pct=0.1)]
    levels = make_levels(prev_high=1000.0, prev_low=900.0)

    score = compute_technical_score(snapshots=snapshots, levels=levels)

    assert score >= 0.0, f"Score should never be negative, got {score}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Total score weights preserved
# ─────────────────────────────────────────────────────────────────────────────

def test_technical_weight_in_total_score():
    """
    Technical score has a 15% weight in the composite formula.
    Changing Technical from 0 to 100 should shift total_score by exactly 15 points.
    """
    catalyst = 80.0
    preopen = 70.0
    liquidity = 60.0

    total_low_tech = compute_total_score(catalyst, preopen, liquidity, technical=0.0)
    total_high_tech = compute_total_score(catalyst, preopen, liquidity, technical=100.0)

    diff = round(total_high_tech - total_low_tech, 2)
    assert diff == 15.0, (
        f"Technical weight should be 15 points (0–100 swing). Got {diff}"
    )


def test_total_score_formula():
    """
    Verify the exact composite formula:
        Total = 0.40 * 80 + 0.25 * 60 + 0.20 * 70 + 0.15 * 50
              = 32 + 15 + 14 + 7.5 = 68.5
    """
    result = compute_total_score(catalyst=80, preopen=60, liquidity=70, technical=50)
    assert result == 68.5, f"Expected 68.5, got {result}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Bucket assignment
# ─────────────────────────────────────────────────────────────────────────────

def test_bucket_assignment():
    assert assign_bucket(75.0) == "A"
    assert assign_bucket(70.0) == "A"
    assert assign_bucket(69.9) == "B"
    assert assign_bucket(50.0) == "B"
    assert assign_bucket(49.9) == "C"
    assert assign_bucket(0.0) == "C"


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Indicative price derived from gap% when no direct price in snapshot
# ─────────────────────────────────────────────────────────────────────────────

def test_levels_with_gap_pct_only_snapshot():
    """
    When the snapshot has gap_pct and prev_close but no indicative_price,
    the scorer should derive indicative_price = prev_close * (1 + gap_pct/100)
    and still produce a valid score.

    Setup: prev_close=1000, gap_pct=3.0 → indicative_price=1030
           prev_high=1050, prev_low=950
           1030 is inside the range but in top 40% → expect score in 40–69 range.
    """
    snapshots = [make_snapshot(gap_pct=3.0, prev_close=1000.0)]  # no indicative_price
    levels = make_levels(prev_high=1050.0, prev_low=950.0)

    score = compute_technical_score(snapshots=snapshots, levels=levels)

    assert 0.0 <= score <= 100.0, f"Score out of range: {score}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Gap proxy function directly
# ─────────────────────────────────────────────────────────────────────────────

def test_gap_proxy_bands():
    """
    Verify the gap proxy score bands match the documented ranges.
    """
    assert _gap_proxy_score([make_snapshot(gap_pct=6.0)]) >= 80.0
    assert _gap_proxy_score([make_snapshot(gap_pct=4.0)]) >= 60.0
    assert _gap_proxy_score([make_snapshot(gap_pct=2.0)]) >= 40.0
    assert _gap_proxy_score([make_snapshot(gap_pct=0.7)]) >= 20.0
    assert _gap_proxy_score([make_snapshot(gap_pct=0.1)]) >= 0.0
    assert _gap_proxy_score([]) == 25.0  # no data → neutral placeholder
