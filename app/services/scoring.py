"""
app/services/scoring.py

Deterministic scoring engine for the Nifty Pre-Market Briefing system.

MILESTONE: 3.5 (Daily OHLC Levels + Improved Technical Scoring)
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

TECHNICAL (0–100, weight 15%)  ← UPDATED in Milestone 3.5

  When prev_high and prev_low are available (loaded via load_daily_levels.py):

  The score is based on WHERE the indicative/pre-open price sits
  relative to the previous day's high/low range.

  Position formula:
      range     = prev_high - prev_low
      position  = (indicative_price - prev_low) / range   (0.0 = at prev_low,
                                                            1.0 = at prev_high)

  Score mapping:
      price > prev_high (breakout zone):
          score = 70 + clamp((price - prev_high) / prev_high * 100, 0, 30)
          i.e. at least 70, up to 100 the further above prev_high

      price < prev_low (breakdown zone):
          score = 70 + clamp((prev_low - price) / prev_low * 100, 0, 30)
          i.e. breakdown is equally strong as breakout structurally

      price inside prev_low..prev_high:
          position in top 20% of range  → score 55–69  (near the high)
          position in bottom 20% of range → score 55–69 (near the low)
          position in middle 60% of range → score 20–54 (inside range, decreasing toward center)

  Plain language examples:
      RELIANCE: prev_high=2500, prev_low=2400, indicative=2520
        → Above prev_high by 0.8%.  Score = 70 + (0.8 * 30/10) = ~72   [Breakout]

      INFY: prev_high=1800, prev_low=1720, indicative=1710
        → Below prev_low by 0.58%.  Score = 70 + (0.58 * 30/10) = ~72  [Breakdown]

      TCS: prev_high=4200, prev_low=4050, indicative=4160
        → Inside range, position = (4160-4050)/(4200-4050) = 0.73  (top 27%)
          → Near the high → score ~62

      WIPRO: prev_high=590, prev_low=560, indicative=575
        → Inside range, position = (575-560)/(590-560) = 0.5 (middle)
          → Deep inside range → score ~35

  Fallback (when levels are not available):
      Uses the gap%-based proxy from Milestone 3.
      gap_pct > 5%  → 80–100  (strong displacement)
      gap_pct 3–5%  → 60–79
      gap_pct 1–3%  → 40–59
      gap_pct 0.5–1% → 20–39
      gap_pct < 0.5% → 10–25
      No data         → 25

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

# ─────────────────────────────────────────────────────────────────────────────
# Events score — AI-backed Catalyst scoring (Milestone 4)
# ─────────────────────────────────────────────────────────────────────────────

# Score bands by (event_type, sentiment) combination
# Each band is (min_score, max_score); we use min_score as the base and
# let confidence scale toward max_score.
_EVENT_SCORE_BANDS: dict[tuple[str, str], tuple[float, float]] = {
    # Strongly positive earnings / guidance / corporate action
    ("EARNINGS", "POSITIVE"):          (80.0, 100.0),
    ("GUIDANCE", "POSITIVE"):          (80.0, 100.0),
    ("CORPORATE_ACTION", "POSITIVE"):  (80.0, 100.0),
    # Broker upgrade
    ("BROKER_RATING", "POSITIVE"):     (75.0, 95.0),
    # Flow events (block/bulk deals, FII buying)
    ("FLOW", "POSITIVE"):              (65.0, 85.0),
    # Neutral earnings / guidance
    ("EARNINGS", "NEUTRAL"):           (45.0, 60.0),
    ("GUIDANCE", "NEUTRAL"):           (45.0, 60.0),
    # Macro events (any sentiment)
    ("MACRO", "POSITIVE"):             (30.0, 50.0),
    ("MACRO", "NEUTRAL"):              (30.0, 50.0),
    ("MACRO", "NEGATIVE"):             (30.0, 50.0),
    # General news (any sentiment)
    ("GENERAL_NEWS", "POSITIVE"):      (25.0, 45.0),
    ("GENERAL_NEWS", "NEUTRAL"):       (25.0, 45.0),
    ("GENERAL_NEWS", "NEGATIVE"):      (25.0, 45.0),
    # Broker downgrade
    ("BROKER_RATING", "NEGATIVE"):     (10.0, 30.0),
    ("BROKER_RATING", "NEUTRAL"):      (20.0, 40.0),
    # Risk events
    ("RISK", "NEGATIVE"):              (5.0,  25.0),
    ("RISK", "NEUTRAL"):               (15.0, 35.0),
    ("RISK", "POSITIVE"):              (20.0, 40.0),  # risk resolved
    # Negative earnings / guidance / corporate action
    ("EARNINGS", "NEGATIVE"):          (5.0,  30.0),
    ("GUIDANCE", "NEGATIVE"):          (5.0,  30.0),
    ("CORPORATE_ACTION", "NEGATIVE"):  (10.0, 35.0),
    # Negative flow (FII selling, large block at discount)
    ("FLOW", "NEGATIVE"):              (10.0, 30.0),
    ("FLOW", "NEUTRAL"):               (25.0, 45.0),
}

_EXTRA_EVENT_BONUS = 5.0   # bonus per additional event of the same tier
_EXTRA_EVENT_CAP  = 15.0  # maximum total bonus from extra events


def compute_events_score(
    classified_events: list[dict],
) -> float:
    """
    Compute an Events score (0–100) from a list of AI-classified event dicts.

    Each dict should have at minimum:
        event_type  (str)  — taxonomy code
        sentiment   (str)  — POSITIVE / NEGATIVE / NEUTRAL
        confidence  (float) — 0.0 to 1.0

    Score logic:
        1. For each event, look up the score band from _EVENT_SCORE_BANDS.
        2. Interpolate within the band using confidence:
               score = min_score + (max_score - min_score) * confidence
        3. Pick the highest single event score as the base score.
        4. Add _EXTRA_EVENT_BONUS for each additional event of the same tier
           as the best event (capped at _EXTRA_EVENT_CAP total bonus).
        5. Return 0.0 if there are no events or all are NO_EVENT.
    """
    if not classified_events:
        return 0.0

    # Filter out NO_EVENT entries
    real_events = [
        e for e in classified_events
        if e.get("event_type") not in ("NO_EVENT", None)
    ]
    if not real_events:
        return 0.0

    # Score each event
    scored = []
    for ev in real_events:
        etype = str(ev.get("event_type", "GENERAL_NEWS")).upper()
        sentiment = str(ev.get("sentiment", "NEUTRAL")).upper()
        confidence = float(ev.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        band = _EVENT_SCORE_BANDS.get(
            (etype, sentiment),
            (25.0, 45.0),  # default: treat as GENERAL_NEWS NEUTRAL
        )
        band_min, band_max = band
        raw_score = band_min + (band_max - band_min) * confidence
        scored.append({
            "event_type": etype,
            "sentiment": sentiment,
            "raw_score": raw_score,
        })

    # Best single event
    scored.sort(key=lambda x: x["raw_score"], reverse=True)
    best = scored[0]
    base_score = best["raw_score"]

    # Count additional events of the same tier (same event_type + sentiment)
    same_tier_extras = sum(
        1 for s in scored[1:]
        if s["event_type"] == best["event_type"] and s["sentiment"] == best["sentiment"]
    )
    bonus = min(same_tier_extras * _EXTRA_EVENT_BONUS, _EXTRA_EVENT_CAP)

    final_score = min(base_score + bonus, 100.0)
    return round(final_score, 2)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Impact classification — keyword matching on headline text
# ─────────────────────────────────────────────────────────────────────────────

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

MEDIUM_IMPACT_PATTERNS = [
    r"\bboard meeting\b",
    r"\bboard\b.*\bapproval\b",
    r"\bfundraising\b",
    r"\bfund raise\b",
    r"\bqip\b",
    r"\bpreferential allotment\b",
    r"\bncd\b",
    r"\bdebenture\b",
    r"\brating\b",
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
    """Stores the breakdown of catalyst scoring for a single symbol."""
    symbol: str
    event_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    best_impact: str = "NONE"
    score: float = 0.0
    matched_keywords: list = field(default_factory=list)


def _classify_event_impact(headline: str, source: str) -> str:
    """
    Classify a single event headline into HIGH / MEDIUM / LOW / NONE.
    Milestone 4 will pass this to an AI classifier and can override the label.
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

    Score logic:
        - No events                → 0
        - Best event is LOW        → base 10, +5 per extra low event (cap 30)
        - Best event is MEDIUM     → base 40, +8 per extra medium event (cap 70)
        - Best event is HIGH       → base 80, +5 per extra high event (cap 100)
    """
    symbol = events[0].get("symbol", "UNKNOWN") if events else "UNKNOWN"
    detail = CatalystDetail(symbol=symbol, event_count=len(events))

    if not events:
        return 0.0, detail

    detail.event_count = len(events)

    for ev in events:
        headline = ev.get("headline", "") or ""
        source = ev.get("source", "") or ""
        priority = ev.get("priority_label") or _classify_event_impact(headline, source)

        if priority == "HIGH":
            detail.high_count += 1
        elif priority == "MEDIUM":
            detail.medium_count += 1
        else:
            detail.low_count += 1

    if detail.high_count > 0:
        detail.best_impact = "HIGH"
    elif detail.medium_count > 0:
        detail.best_impact = "MEDIUM"
    else:
        detail.best_impact = "LOW"

    if detail.best_impact == "HIGH":
        score = min(80 + (detail.high_count - 1) * 5, 100)
    elif detail.best_impact == "MEDIUM":
        score = min(40 + (detail.medium_count - 1) * 8, 70)
    else:
        score = min(10 + (detail.low_count - 1) * 5, 30)

    detail.score = round(score, 2)
    return detail.score, detail


def compute_preopen_score(snapshots: list[dict]) -> float:
    """
    Compute a Pre-open score (0–100) from a list of preopen snapshot dicts.

    Each dict should have at minimum:
        gap_pct (float), indicative_value (float or None)
    """
    if not snapshots:
        return 0.0

    valid = [s for s in snapshots if s.get("gap_pct") is not None]
    if not valid:
        return 0.0

    avg_gap_abs = sum(abs(s["gap_pct"]) for s in valid) / len(valid)

    if avg_gap_abs < 1.5:
        base_score = (avg_gap_abs / 1.5) * 20.0
    elif avg_gap_abs < 3.0:
        base_score = 30.0 + ((avg_gap_abs - 1.5) / 1.5) * 30.0
    elif avg_gap_abs < 6.0:
        base_score = 60.0 + ((avg_gap_abs - 3.0) / 3.0) * 25.0
    elif avg_gap_abs < 8.0:
        base_score = 85.0 + ((avg_gap_abs - 6.0) / 2.0) * 10.0
    else:
        base_score = 95.0

    values = [s.get("indicative_value") for s in valid if s.get("indicative_value")]
    if values:
        avg_value = sum(values) / len(values)
        if avg_value < 10_000_000:
            base_score = min(base_score, 50.0)
        elif avg_value > 200_000_000:
            bonus = min((avg_value - 200_000_000) / 200_000_000 * 10, 10.0)
            base_score = min(base_score + bonus, 100.0)

    return round(base_score, 2)


def compute_liquidity_score(symbol_data: dict) -> float:
    """
    Compute a Liquidity score (0–100) from symbol metadata.

    symbol_data dict should have:
        avg_daily_value_20d (float, in INR crores — may be None)
        is_fno (bool)
    """
    adv = symbol_data.get("avg_daily_value_20d")
    is_fno = bool(symbol_data.get("is_fno", False))

    if adv is None:
        base_score = 30.0
    elif adv >= 500:
        base_score = 85.0
    elif adv >= 200:
        base_score = 65.0 + ((adv - 200) / 300) * 19.0
    elif adv >= 50:
        base_score = 40.0 + ((adv - 50) / 150) * 24.0
    elif adv >= 10:
        base_score = 15.0 + ((adv - 10) / 40) * 24.0
    else:
        base_score = max(0.0, (adv / 10) * 14.0)

    if is_fno:
        base_score = min(base_score + 10.0, 100.0)

    return round(base_score, 2)


def compute_technical_score(
    snapshots: list[dict],
    levels: Optional[dict] = None,
) -> float:
    """
    Compute a Technical context score (0–100).

    MILESTONE 3.5 UPDATE: Now uses prev_high/prev_low when available.

    Parameters
    ----------
    snapshots : list[dict]
        Pre-open snapshots for this symbol on the trade date.
        Each dict should have at minimum: gap_pct, indicative_price, prev_close.

    levels : dict or None
        Previous-day OHLC levels dict with keys: prev_high, prev_low, prev_close.
        Loaded from the daily_levels table by the ranking pipeline.
        If None or missing keys, the function falls back to gap%-based scoring.

    Returns
    -------
    float
        Technical score in the range 0–100.

    ────────────────────────────────────────────────────────────────────────
    When levels ARE available (preferred path):
    ────────────────────────────────────────────────────────────────────────

    The score reflects WHERE the current indicative price sits relative to
    yesterday's high/low range. The idea is simple:

      - If today's indicative price is ABOVE yesterday's high → the stock
        is breaking out of its previous range → strong technical setup.
        Score: 70 to 100 (higher the further above prev_high).

      - If today's indicative price is BELOW yesterday's low → the stock
        is breaking down below its previous range → also strong structural
        context (just on the short/breakdown side).
        Score: 70 to 100 (higher the further below prev_low).

      - If the price is INSIDE yesterday's range:
          - Near the top 20% of the range → moderate-high score (55–69).
            The price is approaching but hasn't yet cleared the prior high.
          - Near the bottom 20% of the range → moderate-high score (55–69).
            The price is near prior support, which is also meaningful.
          - In the middle 60% → lower score (20–54), linearly decreasing
            toward the center. A price stuck in the middle of its prior
            range has no clear technical context for the day.

    Score formula detail:
        range = prev_high - prev_low
        position = (indicative_price - prev_low) / range

        Breakout:  score = min(70 + (overshoot_pct * 3.0), 100)
        Breakdown: score = min(70 + (undershoot_pct * 3.0), 100)
        Top 20% of range:    score = 55 + (position - 0.80) / 0.20 * 14  → 55 to 69
        Bottom 20% of range: score = 55 + (0.20 - position) / 0.20 * 14  → 55 to 69
        Middle 60%:          score = 20 + (0.5 - abs(position - 0.5)) / 0.3 * 34
                             (20 at center, up to 54 approaching the quartiles)

    ────────────────────────────────────────────────────────────────────────
    Fallback when levels are NOT available (gap%-based proxy from M3):
    ────────────────────────────────────────────────────────────────────────

    gap_pct > 5%    → 80–100  (strong displacement, likely at a key level)
    gap_pct 3–5%   → 60–79
    gap_pct 1–3%   → 40–59
    gap_pct 0.5–1% → 20–39
    gap_pct < 0.5% → 10–25
    No data         → 25 (neutral placeholder)
    """

    # ── Path A: Use prev_high / prev_low if available ─────────────────────
    if levels:
        prev_high = levels.get("prev_high")
        prev_low = levels.get("prev_low")

        if prev_high and prev_low and prev_high > prev_low:
            # Get indicative_price from the most recent valid snapshot
            indicative_price = None

            if snapshots:
                valid_snaps = [s for s in snapshots if s.get("indicative_price") is not None]
                if valid_snaps:
                    indicative_price = valid_snaps[-1]["indicative_price"]

            # If no indicative price in snapshots, try to derive from gap% + prev_close
            if indicative_price is None and snapshots:
                valid_gap = [s for s in snapshots if s.get("gap_pct") is not None and s.get("prev_close")]
                if valid_gap:
                    last = valid_gap[-1]
                    indicative_price = last["prev_close"] * (1 + last["gap_pct"] / 100)

            if indicative_price is None:
                # No price data at all — fall back to gap proxy
                logger.debug("levels present but no indicative_price; falling back to gap proxy")
                return _gap_proxy_score(snapshots)

            price_range = prev_high - prev_low

            # Breakout: above prev_high
            if indicative_price > prev_high:
                overshoot_pct = (indicative_price - prev_high) / prev_high * 100
                score = min(70.0 + overshoot_pct * 3.0, 100.0)
                logger.debug(
                    "Technical [BREAKOUT] price=%.2f prev_high=%.2f overshoot=%.2f%% score=%.1f",
                    indicative_price, prev_high, overshoot_pct, score,
                )
                return round(score, 2)

            # Breakdown: below prev_low
            if indicative_price < prev_low:
                undershoot_pct = (prev_low - indicative_price) / prev_low * 100
                score = min(70.0 + undershoot_pct * 3.0, 100.0)
                logger.debug(
                    "Technical [BREAKDOWN] price=%.2f prev_low=%.2f undershoot=%.2f%% score=%.1f",
                    indicative_price, prev_low, undershoot_pct, score,
                )
                return round(score, 2)

            # Inside range
            position = (indicative_price - prev_low) / price_range
            # position: 0.0 = at prev_low, 1.0 = at prev_high

            if position >= 0.80:
                # Top 20% of range — near the high
                score = 55.0 + (position - 0.80) / 0.20 * 14.0
                logger.debug(
                    "Technical [NEAR_HIGH] price=%.2f pos=%.2f score=%.1f",
                    indicative_price, position, score,
                )
            elif position <= 0.20:
                # Bottom 20% of range — near the low
                score = 55.0 + (0.20 - position) / 0.20 * 14.0
                logger.debug(
                    "Technical [NEAR_LOW] price=%.2f pos=%.2f score=%.1f",
                    indicative_price, position, score,
                )
            else:
                # Middle 60% — inside range, linearly decreasing toward center
                # At position=0.80 or 0.20: score approaches ~54
                # At position=0.50 (dead center): score = 20
                distance_from_center = abs(position - 0.5)  # 0 at center, 0.3 at edges of middle
                score = 20.0 + (distance_from_center / 0.3) * 34.0
                score = min(score, 54.0)
                logger.debug(
                    "Technical [INSIDE_RANGE] price=%.2f pos=%.2f dist=%.2f score=%.1f",
                    indicative_price, position, distance_from_center, score,
                )

            return round(score, 2)

    # ── Path B: Gap-based fallback (no levels available) ──────────────────
    logger.debug("No daily levels found — using gap%% proxy for Technical score.")
    return _gap_proxy_score(snapshots)


def _gap_proxy_score(snapshots: list[dict]) -> float:
    """
    Gap%-based Technical proxy — used when prev_high/prev_low are not available.
    This was the only Technical scorer before Milestone 3.5.

    Score bands by |gap_pct|:
        > 5%    → 80–100  (strongly at a breakout/breakdown level)
        3–5%   → 60–79
        1–3%   → 40–59
        0.5–1% → 20–39
        < 0.5% → 10–25
        No data → 25
    """
    if not snapshots:
        return 25.0

    valid = [s for s in snapshots if s.get("gap_pct") is not None]
    if not valid:
        return 25.0

    latest = valid[-1]
    gap_abs = abs(latest["gap_pct"])

    if gap_abs > 5.0:
        score = min(80.0 + ((gap_abs - 5.0) / 5.0) * 20.0, 100.0)
    elif gap_abs > 3.0:
        score = 60.0 + ((gap_abs - 3.0) / 2.0) * 19.0
    elif gap_abs > 1.0:
        score = 40.0 + ((gap_abs - 1.0) / 2.0) * 19.0
    elif gap_abs > 0.5:
        score = 20.0 + ((gap_abs - 0.5) / 0.5) * 19.0
    else:
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
    """
    if total_score >= 70:
        return "A"
    elif total_score >= 50:
        return "B"
    else:
        return "C"
