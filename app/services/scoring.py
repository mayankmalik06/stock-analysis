"""
app/services/scoring.py

Deterministic scoring engine for the Nifty Pre-Market Briefing system.

MILESTONE: 3 (Scoring and Ranking)
STATUS: Implemented

Scoring formula (from spec):
  Total Score = 0.40(Catalyst) + 0.25(Pre-open) + 0.20(Liquidity) + 0.15(Technical)

Each component returns a score from 0 to 100.
AI does NOT determine scores — this module does entirely with rules.

──────────────────────────────────────────────────────────────────────────────
Plain-language explanation of each scorer
──────────────────────────────────────────────────────────────────────────────

CATALYST (0–100, weight 40%)
  Looks at all events for a symbol in the last N hours (default 24h).
  Classifies each event as HIGH / MEDIUM / LOW impact using keyword matching
  on the headline and source fields.
  - At least one HIGH-impact event → score in 80–100 range
  - Only MEDIUM events → score in 40–70 range
  - Only LOW events → score in 10–30 range
  - No events → 0
  Multiple events of the same tier boost the score slightly (up to a cap).

PRE-OPEN (0–100, weight 25%)
  Uses snapshots from preopen_snapshots for the latest session of the symbol.
  Base score comes from the absolute gap% (how far the indicative price moved
  from yesterday's close):
    < 1.5%    → base 0–20  (weak reaction)
    1.5–3%   → base 30–60  (moderate reaction)
    3–6%     → base 60–85  (strong reaction)
    > 6%     → base 85–100 (very strong, with a mild cap above 8%)
  Then adjusts for relative indicative value vs a typical threshold:
    If indicative_value is very low (< 10 Cr)  → cap the score at 50
    If indicative_value is high (> 200 Cr)     → add up to 10 bonus points
  If multiple snapshots exist, uses average gap% for stability.

LIQUIDITY (0–100, weight 20%)
  Pulls from symbols.avg_daily_value_20d and symbols.is_fno.
  Tiered by average daily traded value (in INR crores):
    >= 500 Cr  → base 85–100
    200–499 Cr → base 65–84
    50–199 Cr  → base 40–64
    10–49 Cr   → base 15–39
    < 10 Cr    → base 0–14
  F&O inclusion adds +10 bonus points (capped at 100).
  If avg_daily_value_20d is missing, uses a safe default of 30 (mid-low).

TECHNICAL (0–100, weight 15%)
  Uses the indicative_price from the latest pre-open snapshot vs the
  previous day's high/low implied from snapshots (using prev_close as proxy).
  Formula proxy (until real high/low fields are stored):
    gap_pct > 0 and indicative_price > prev_close → near breakout zone
    gap_pct < 0 and indicative_price < prev_close → near breakdown zone
    Extreme gaps (>3%) get a higher technical score because they suggest
    price is at or beyond a key level.
  Score bands:
    gap_pct > 5%  → 80–100  (clear breakout territory)
    gap_pct 3–5%  → 60–79   (near breakout)
    gap_pct 1–3%  → 40–59   (approaching but not at level)
    gap_pct < 1%  → 20–39   (no obvious technical setup)
    No snapshot    → 25      (neutral placeholder)
  Note: direction does not matter here — both large gaps up AND down get
  higher technical scores because both represent strong price displacement.

COMPOSITE (0–100)
  total = 0.40 * catalyst + 0.25 * preopen + 0.20 * liquidity + 0.15 * technical
  Rounded to 2 decimal places.

BUCKETS
  A: total_score >= 70
  B: 50 <= total_score < 70
  C: total_score < 50
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Impact classification — keyword matching on headline text
# These keyword lists map to HIGH / MEDIUM / LOW impact buckets.
# Milestone 4 will replace or augment this with AI classification.
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that strongly suggest a HIGH-impact catalyst
HIGH_IMPACT_PATTERNS = [
    r"\bearnings\b",
    r"\bresult[s]?\b",
    r"\bquarterly result\b",
    r"\bq[1-4]\s*(fy)?\d*\s*result\b",
    r"\bnet profit\b",
    r"\brevenue\b.*\bgrowth\b",
    r"\bguidance\b",
    r"\border win\b",
    r"\blarge order\b",
    r"\bmerger\b",
    r"\bacquisition\b",
    r"\bm&a\b",
    r"\btakeover\b",
    r"\bamalgamation\b",
    r"\bde-?merger\b",
    r"\bpromoter\b.*\bpledge\b",
    r"\bsebi\b.*\border\b",
    r"\bsebi\b.*\bpenalt\b",
    r"\bregulatory\b.*\border\b",
    r"\bblock deal\b",
    r"\bbulk deal\b",
    r"\bcredit rating\b.*\bupgrade\b",
    r"\brating\b.*\bupgrade\b",
    r"\bcredit rating\b.*\bdowngrade\b",
    r"\brating\b.*\bdowngrade\b",
    r"\binsolvency\b",
    r"\bnclt\b",
    r"\bbankruptcy\b",
    r"\bopen offer\b",
    r"\bbuyback\b",
    r"\bdelisting\b",
    r"\bsplit\b",
    r"\bbonus issue\b",
    r"\bright[s]? issue\b",
    r"\bdividend\b",
    r"\bspecial dividend\b",
    r"\binterim dividend\b",
    r"\bcapex\b.*\bplan\b",
    r"\binvestment\b.*\bapproval\b",
    r"\bjoint venture\b",
    r"\bstrategic partnership\b",
]

# Patterns for MEDIUM-impact events
MEDIUM_IMPACT_PATTERNS = [
    r"\bboard meeting\b",
    r"\bboard\b.*\bapproval\b",
    r"\bfundraising\b",
    r"\bfund raise\b",
    r"\bqip\b",
    r"\bpreferential allotment\b",
    r"\bncd\b",
    r"\bdebenture\b",
    r"\brating\b",          # generic rating mention without upgrade/downgrade
    r"\banalyst\b",
    r"\bbroker\b.*\breport\b",
    r"\btarget price\b",
    r"\binitiating coverage\b",
    r"\bsubsidiary\b",
    r"\bjoint venture\b.*\bupdate\b",
    r"\bconcall\b",
    r"\binvestor\b.*\bday\b",
    r"\bagm\b",
    r"\begm\b",
    r"\bannual general meeting\b",
    r"\bappoint\b",
    r"\bresign\b",
    r"\bmanagement change\b",
    r"\bmd\b.*\bappointed\b",
    r"\bceo\b.*\bappointed\b",
    r"\bfii\b.*\bstake\b",
    r"\bdii\b.*\bstake\b",
]

# Anything that doesn't match HIGH or MEDIUM falls to LOW.
# LOW patterns are used for explicit detection only (optional).
LOW_IMPACT_PATTERNS = [
    r"\bgeneral update\b",
    r"\bregulatory filing\b",
    r"\bclearing\b",
    r"\bstock exchange\b.*\bnotice\b",
    r"\bcompliance\b",
    r"\bdisclosure\b",
]


@dataclass
class CatalystDetail:
    """
    Stores the breakdown of catalyst scoring for a single symbol.
    Useful for debugging, logging, and future AI explanation.
    """
    symbol: str
    event_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    best_impact: str = "NONE"     # "HIGH", "MEDIUM", "LOW", or "NONE"
    score: float = 0.0
    matched_keywords: list = field(default_factory=list)


def _classify_event_impact(headline: str, source: str) -> str:
    """
    Classify a single event headline into HIGH / MEDIUM / LOW / NONE.

    Uses regex pattern matching on the lower-cased headline and source.
    Returns the impact level as a string.

    Milestone 4 will pass this to an AI classifier and can override the label.
    Until then, these keyword rules are the classification engine.
    """
    text = (headline + " " + source).lower()

    for pattern in HIGH_IMPACT_PATTERNS:
        if re.search(pattern, text):
            return "HIGH"

    for pattern in MEDIUM_IMPACT_PATTERNS:
        if re.search(pattern, text):
            return "MEDIUM"

    return "LOW"


def compute_catalyst_score(
    events: list[dict],
    window_hours: int = 24,
) -> tuple[float, CatalystDetail]:
    """
    Compute a Catalyst score (0–100) from a list of event dicts.

    Each dict should have at minimum:
        headline (str), source (str), event_timestamp (datetime or None)

    Returns:
        (score: float, detail: CatalystDetail)

    Score logic:
        - No events                → 0
        - Best event is LOW        → base 10, +5 per extra low event (cap 30)
        - Best event is MEDIUM     → base 40, +8 per extra medium event (cap 70)
        - Best event is HIGH       → base 80, +5 per extra high event (cap 100)
        - High overrides medium, medium overrides low.
    """
    symbol = events[0].get("symbol", "UNKNOWN") if events else "UNKNOWN"
    detail = CatalystDetail(symbol=symbol, event_count=len(events))

    if not events:
        return 0.0, detail

    detail.event_count = len(events)

    for ev in events:
        headline = ev.get("headline", "") or ""
        source = ev.get("source", "") or ""

        # Use pre-classified priority_label if already set (from prior AI run)
        # Otherwise fall back to our keyword classifier.
        priority = ev.get("priority_label") or _classify_event_impact(headline, source)

        if priority == "HIGH":
            detail.high_count += 1
        elif priority == "MEDIUM":
            detail.medium_count += 1
        else:
            detail.low_count += 1

    # Determine best impact tier
    if detail.high_count > 0:
        detail.best_impact = "HIGH"
    elif detail.medium_count > 0:
        detail.best_impact = "MEDIUM"
    else:
        detail.best_impact = "LOW"

    # Score calculation
    if detail.best_impact == "HIGH":
        # Base 80, each extra HIGH event adds 5, cap at 100
        score = min(80 + (detail.high_count - 1) * 5, 100)
    elif detail.best_impact == "MEDIUM":
        # Base 40, each extra MEDIUM event adds 8, cap at 70
        score = min(40 + (detail.medium_count - 1) * 8, 70)
    else:
        # LOW only: base 10, each extra LOW event adds 5, cap at 30
        score = min(10 + (detail.low_count - 1) * 5, 30)

    detail.score = round(score, 2)
    return detail.score, detail


def compute_preopen_score(snapshots: list[dict]) -> float:
    """
    Compute a Pre-open score (0–100) from a list of preopen snapshot dicts.

    Each dict should have at minimum:
        gap_pct (float), indicative_value (float or None)

    Score logic:
        Base score from average |gap_pct|:
            < 1.5%   → linear scale 0–20   (weak)
            1.5–3%  → linear scale 30–60  (moderate)
            3–6%    → linear scale 60–85  (strong)
            6–8%    → linear scale 85–95  (very strong)
            > 8%    → 95, with a tiny cap to avoid over-rewarding extreme gaps

        Value adjustment:
            If avg indicative_value < 10 Cr  → cap score at 50 (illiquid open)
            If avg indicative_value > 200 Cr → add up to 10 bonus points

    Using the average across snapshots smooths out early-session noise.
    """
    if not snapshots:
        return 0.0

    # Filter out snapshots with no gap_pct
    valid = [s for s in snapshots if s.get("gap_pct") is not None]
    if not valid:
        return 0.0

    avg_gap_abs = sum(abs(s["gap_pct"]) for s in valid) / len(valid)

    # Base score from gap magnitude
    if avg_gap_abs < 1.5:
        # 0 at gap=0, up to 20 at gap=1.5
        base_score = (avg_gap_abs / 1.5) * 20.0

    elif avg_gap_abs < 3.0:
        # 30 at gap=1.5, up to 60 at gap=3.0
        base_score = 30.0 + ((avg_gap_abs - 1.5) / 1.5) * 30.0

    elif avg_gap_abs < 6.0:
        # 60 at gap=3, up to 85 at gap=6
        base_score = 60.0 + ((avg_gap_abs - 3.0) / 3.0) * 25.0

    elif avg_gap_abs < 8.0:
        # 85 at gap=6, up to 95 at gap=8
        base_score = 85.0 + ((avg_gap_abs - 6.0) / 2.0) * 10.0

    else:
        # Cap extreme gaps at 95 (avoid over-weighting runaway opens)
        base_score = 95.0

    # Indicative value adjustment
    # Use average across snapshots that have a value
    values = [s.get("indicative_value") for s in valid if s.get("indicative_value")]
    if values:
        avg_value = sum(values) / len(values)

        if avg_value < 10_000_000:  # < 10 Cr (stored as INR, not crores)
            # Very thin open — cap at 50
            base_score = min(base_score, 50.0)

        elif avg_value > 200_000_000:  # > 200 Cr
            # High value open — add up to 10 points
            bonus = min((avg_value - 200_000_000) / 200_000_000 * 10, 10.0)
            base_score = min(base_score + bonus, 100.0)

    return round(base_score, 2)


def compute_liquidity_score(symbol_data: dict) -> float:
    """
    Compute a Liquidity score (0–100) from symbol metadata.

    symbol_data dict should have:
        avg_daily_value_20d (float, in INR crores — may be None)
        is_fno (bool)

    Score logic:
        Base score from avg_daily_value_20d (INR crores):
            >= 500  → 85
            200–499 → 65 + linear scale up to 84
            50–199  → 40 + linear scale up to 64
            10–49   → 15 + linear scale up to 39
            < 10    → 5 + small scale up to 14

        F&O bonus: +10 if is_fno is True (capped at 100)

        If avg_daily_value_20d is None/missing:
            → use a default score of 30 (mid-low, not rewarded, not penalised)
              This is a placeholder until real data is fetched.
    """
    adv = symbol_data.get("avg_daily_value_20d")  # in INR crores
    is_fno = bool(symbol_data.get("is_fno", False))

    if adv is None:
        # No data yet — safe neutral placeholder
        # Comment: replace this when avg_daily_value_20d is populated
        base_score = 30.0
    elif adv >= 500:
        base_score = 85.0
    elif adv >= 200:
        # 65 at 200 Cr, 84 at ~499 Cr
        base_score = 65.0 + ((adv - 200) / 300) * 19.0
    elif adv >= 50:
        # 40 at 50 Cr, 64 at ~199 Cr
        base_score = 40.0 + ((adv - 50) / 150) * 24.0
    elif adv >= 10:
        # 15 at 10 Cr, 39 at ~49 Cr
        base_score = 15.0 + ((adv - 10) / 40) * 24.0
    else:
        # Very illiquid: 0–14
        base_score = max(0.0, (adv / 10) * 14.0)

    # F&O inclusion bonus
    if is_fno:
        base_score = min(base_score + 10.0, 100.0)

    return round(base_score, 2)


def compute_technical_score(snapshots: list[dict]) -> float:
    """
    Compute a Technical context score (0–100) using pre-open snapshot data.

    This is a PROXY implementation for Milestone 3.
    Real previous-day high/low fields are not yet stored; we use gap_pct
    as a stand-in for price displacement relative to key levels.

    Rationale:
        A large gap (up or down) means the indicative price has moved
        decisively away from the previous close, which is often at or
        beyond the previous day's high/low — a technically significant zone.

    Score bands (by |gap_pct|):
        > 5%    → 80–100  (strongly at a breakout/breakdown level)
        3–5%   → 60–79   (near a key level)
        1–3%   → 40–59   (some displacement, moderate technical context)
        0.5–1% → 20–39   (mild gap, no obvious level)
        < 0.5% → 10–25   (flat open, no technical context)
        No data → 25 (neutral placeholder)

    Note: This component will be significantly improved in a later milestone
    when we store actual previous-day high/low/VWAP in snapshots or a
    separate price-levels table.
    """
    if not snapshots:
        # No pre-open data — return a neutral midpoint placeholder
        return 25.0

    valid = [s for s in snapshots if s.get("gap_pct") is not None]
    if not valid:
        return 25.0

    # Use the last snapshot (most recent = most settled indicative price)
    latest = valid[-1]
    gap_abs = abs(latest["gap_pct"])

    if gap_abs > 5.0:
        # Strong move into breakout/breakdown territory
        # Scale from 80 at 5% to 100 at ~10%
        score = min(80.0 + ((gap_abs - 5.0) / 5.0) * 20.0, 100.0)

    elif gap_abs > 3.0:
        # Approaching or touching a key level
        score = 60.0 + ((gap_abs - 3.0) / 2.0) * 19.0

    elif gap_abs > 1.0:
        # Mild displacement
        score = 40.0 + ((gap_abs - 1.0) / 2.0) * 19.0

    elif gap_abs > 0.5:
        # Small gap — limited technical context
        score = 20.0 + ((gap_abs - 0.5) / 0.5) * 19.0

    else:
        # Flat open — minimal technical context
        score = max(10.0, gap_abs / 0.5 * 10.0)

    return round(score, 2)


def compute_total_score(
    catalyst: float,
    preopen: float,
    liquidity: float,
    technical: float,
) -> float:
    """
    Weighted composite score.

    Formula (from spec):
        Total = 0.40 * Catalyst + 0.25 * Pre-open + 0.20 * Liquidity + 0.15 * Technical

    All inputs should be in the range 0–100.
    Output is rounded to 2 decimal places.
    """
    return round(
        0.40 * catalyst +
        0.25 * preopen +
        0.20 * liquidity +
        0.15 * technical,
        2,
    )


def assign_bucket(total_score: float) -> str:
    """
    Assign a watchlist bucket label based on total_score.

    A: total_score >= 70  → highest priority, trade candidates
    B: 50 <= score < 70  → secondary watch, needs confirmation
    C: score < 50         → watch-only or ignore

    Note: The spec doc mentions 75/60 as thresholds but the task brief
    uses 70/50. We use 70/50 as instructed in the Milestone 3 spec.
    Both are configurable — change the numbers here to tune.
    """
    if total_score >= 70:
        return "A"
    elif total_score >= 50:
        return "B"
    else:
        return "C"
