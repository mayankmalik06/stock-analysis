"""
app/ai/event_classifier.py

AI-powered event classifier for the Nifty Pre-Market Briefing system.

MILESTONE: 4 (AI Layer)
STATUS: Implemented

Responsibility:
    Takes a symbol, trade_date, and raw event text (headline + description).
    Returns a structured classification:
        event_type  — one of the approved taxonomy codes
        sentiment   — POSITIVE, NEGATIVE, or NEUTRAL
        confidence  — float 0.0–1.0
        label       — user-facing one-line description of the event

Rules:
    - One LLM call per event.
    - Temperature 0.1 for stability.
    - Strict JSON schema in the response — parsed and validated before use.
    - If raw_text is blank or None → returns NO_EVENT without calling the LLM.
    - If LLM_API_KEY is missing → dry-run mode returns a mock result.
    - AI never ranks stocks. AI classifies events only.

Taxonomy:
    EARNINGS          — Quarterly/annual results, profit, revenue
    GUIDANCE          — Forward guidance, outlook revision
    BROKER_RATING     — Analyst upgrade/downgrade/initiation, target price
    MACRO             — RBI policy, Budget, global macro, sector events
    CORPORATE_ACTION  — Dividend, buyback, split, bonus, rights, delisting
    FLOW              — Block/bulk deals, FII/DII stake changes
    RISK              — Regulatory orders, SEBI actions, legal, management issues
    GENERAL_NEWS      — Press releases, business updates (no specific category)
    NO_EVENT          — No meaningful text present — do not fabricate

Usage:
    from app.ai.event_classifier import classify_event

    result = classify_event(
        symbol="RELIANCE",
        trade_date="2026-06-25",
        raw_text="Reliance Q4FY26 results: Net profit up 18% YoY to Rs 19,000 Cr",
    )
    # result = {
    #     "event_type": "EARNINGS",
    #     "sentiment": "POSITIVE",
    #     "confidence": 0.97,
    #     "label": "Reliance Q4 net profit +18% YoY — strong earnings beat",
    # }
"""

import json
import logging
from typing import Optional

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# ── Approved taxonomy values ─────────────────────────────────────────────────
VALID_EVENT_TYPES = {
    "EARNINGS",
    "GUIDANCE",
    "BROKER_RATING",
    "MACRO",
    "CORPORATE_ACTION",
    "FLOW",
    "RISK",
    "GENERAL_NEWS",
    "NO_EVENT",
}

VALID_SENTIMENTS = {"POSITIVE", "NEGATIVE", "NEUTRAL"}

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert Indian stock market event classifier.

Your job is to classify a corporate news event for an NSE-listed stock into a structured format.

You MUST return ONLY a valid JSON object with exactly these four fields:
  "event_type"  — one of: EARNINGS, GUIDANCE, BROKER_RATING, MACRO, CORPORATE_ACTION, FLOW, RISK, GENERAL_NEWS, NO_EVENT
  "sentiment"   — one of: POSITIVE, NEGATIVE, NEUTRAL
  "confidence"  — a float between 0.0 and 1.0 representing your classification confidence
  "label"       — a single user-facing sentence (max 120 chars) describing the event clearly and factually

Taxonomy definitions:
  EARNINGS         — Quarterly or annual results, net profit, revenue, EPS announcements
  GUIDANCE         — Management guidance, outlook revision, future revenue/margin projections
  BROKER_RATING    — Analyst upgrade, downgrade, initiation of coverage, target price change
  MACRO            — RBI policy, Budget announcements, global macro, sector-wide regulatory events
  CORPORATE_ACTION — Dividend, buyback, stock split, bonus issue, rights issue, open offer, delisting
  FLOW             — Block deals, bulk deals, FII/DII stake changes, promoter transactions
  RISK             — SEBI regulatory action, court order, legal dispute, fraud allegation, management crisis
  GENERAL_NEWS     — Any news that doesn't fit the above categories clearly
  NO_EVENT         — The text is blank, meaningless, or contains no classifiable event

Rules:
  1. If the input text is empty, very short (<10 chars), or contains no real event information, return NO_EVENT with confidence 1.0.
  2. Never fabricate or infer facts not present in the input text.
  3. For BROKER_RATING: upgrade/positive initiation → POSITIVE; downgrade → NEGATIVE; hold/neutral → NEUTRAL.
  4. For EARNINGS: beat or profit growth → POSITIVE; miss or loss → NEGATIVE; in-line → NEUTRAL.
  5. For RISK events, sentiment is usually NEGATIVE unless the risk is resolved.
  6. For CORPORATE_ACTION: dividend/buyback/bonus → POSITIVE; delisting or rights at discount → context-dependent.
  7. Keep the label factual and concise — it will be shown directly to a trader.
  8. Return ONLY the JSON object. No preamble, no explanation, no markdown fences.

Example output:
{"event_type": "EARNINGS", "sentiment": "POSITIVE", "confidence": 0.95, "label": "Q4 net profit +18% YoY, beat consensus estimates by 4%"}"""


# ── User prompt template ──────────────────────────────────────────────────────
def _build_user_prompt(symbol: str, trade_date: str, raw_text: str) -> str:
    return (
        f"Symbol: {symbol}\n"
        f"Trade date: {trade_date}\n"
        f"Event text: {raw_text.strip()}"
    )


# ── Mock result for dry-run mode ──────────────────────────────────────────────
def _mock_result(raw_text: str) -> dict:
    """
    Returns a deterministic mock classification when no API key is configured.
    Used for offline testing and CI environments.
    """
    text_lower = raw_text.lower()

    if not raw_text.strip():
        return {
            "event_type": "NO_EVENT",
            "sentiment": "NEUTRAL",
            "confidence": 1.0,
            "label": "No event text provided",
            "_mock": True,
        }

    # Simple keyword-based mock so tests are predictable
    if any(k in text_lower for k in ["result", "profit", "revenue", "earnings", "q4", "q1", "q2", "q3"]):
        event_type = "EARNINGS"
        sentiment = "POSITIVE" if any(k in text_lower for k in ["up", "growth", "beat", "rise", "gain"]) else "NEUTRAL"
    elif any(k in text_lower for k in ["guidance", "outlook", "forecast"]):
        event_type = "GUIDANCE"
        sentiment = "POSITIVE"
    elif any(k in text_lower for k in ["upgrade", "downgrade", "target price", "initiating", "analyst", "broker"]):
        event_type = "BROKER_RATING"
        sentiment = "POSITIVE" if "upgrade" in text_lower or "initiating" in text_lower else "NEGATIVE"
    elif any(k in text_lower for k in ["dividend", "buyback", "split", "bonus", "rights"]):
        event_type = "CORPORATE_ACTION"
        sentiment = "POSITIVE"
    # RISK check before FLOW: sebi/regulatory/fraud/legal/penalty take priority over promoter/flow keywords
    elif any(k in text_lower for k in ["sebi", "regulatory action", "penalty", "legal", "fraud", "nclt", "insolvency", "bankruptcy"]):
        event_type = "RISK"
        sentiment = "NEGATIVE"
    elif any(k in text_lower for k in ["block deal", "bulk deal", "fii", "dii", "promoter"]):
        event_type = "FLOW"
        sentiment = "POSITIVE"
    elif any(k in text_lower for k in ["rbi", "budget", "macro", "sector"]):
        event_type = "MACRO"
        sentiment = "NEUTRAL"
    else:
        event_type = "GENERAL_NEWS"
        sentiment = "NEUTRAL"

    label = raw_text.strip()[:120]
    return {
        "event_type": event_type,
        "sentiment": sentiment,
        "confidence": 0.75,
        "label": label,
        "_mock": True,
    }


# ── JSON validation ───────────────────────────────────────────────────────────
def _validate_and_fix(raw: dict, symbol: str) -> dict:
    """
    Validates the LLM response dict and applies safe fallbacks for any
    invalid or missing field. Never raises — always returns a usable dict.
    """
    event_type = str(raw.get("event_type", "GENERAL_NEWS")).upper().strip()
    if event_type not in VALID_EVENT_TYPES:
        logger.warning(
            "Classifier returned unknown event_type '%s' for %s — defaulting to GENERAL_NEWS",
            event_type, symbol,
        )
        event_type = "GENERAL_NEWS"

    sentiment = str(raw.get("sentiment", "NEUTRAL")).upper().strip()
    if sentiment not in VALID_SENTIMENTS:
        logger.warning(
            "Classifier returned unknown sentiment '%s' for %s — defaulting to NEUTRAL",
            sentiment, symbol,
        )
        sentiment = "NEUTRAL"

    try:
        confidence = float(raw.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))  # clamp to [0, 1]
    except (TypeError, ValueError):
        confidence = 0.5
        logger.warning("Classifier returned invalid confidence for %s — using 0.5", symbol)

    label = str(raw.get("label", "")).strip()
    if not label:
        label = f"{event_type.replace('_', ' ').title()} event for {symbol}"
    label = label[:300]  # enforce column width

    return {
        "event_type": event_type,
        "sentiment": sentiment,
        "confidence": confidence,
        "label": label,
    }


# ── Main classifier function ──────────────────────────────────────────────────
def classify_event(
    symbol: str,
    trade_date: str,
    raw_text: str,
) -> dict:
    """
    Classify a single event using the LLM.

    Parameters
    ----------
    symbol     : NSE ticker, e.g. "RELIANCE"
    trade_date : ISO date string, e.g. "2026-06-25"
    raw_text   : the event headline + description to classify

    Returns
    -------
    dict with keys:
        event_type  (str)   — taxonomy code
        sentiment   (str)   — POSITIVE / NEGATIVE / NEUTRAL
        confidence  (float) — 0.0 to 1.0
        label       (str)   — user-facing one-line description
        _mock       (bool)  — True if this was a dry-run mock (absent in live calls)

    Never raises. Returns NO_EVENT dict on any unrecoverable error.
    """
    # ── Guard: empty text → NO_EVENT without LLM call ────────────────────
    if not raw_text or not raw_text.strip():
        logger.debug("Empty raw_text for %s on %s — returning NO_EVENT.", symbol, trade_date)
        return {
            "event_type": "NO_EVENT",
            "sentiment": "NEUTRAL",
            "confidence": 1.0,
            "label": "No event text provided",
        }

    # ── Guard: no API key → dry-run mock ─────────────────────────────────
    if not settings.llm_api_key:
        logger.info(
            "LLM_API_KEY not set — using mock classifier for %s on %s.",
            symbol, trade_date,
        )
        return _mock_result(raw_text)

    # ── Live LLM call ─────────────────────────────────────────────────────
    try:
        client = OpenAI(api_key=settings.llm_api_key)

        response = client.chat.completions.create(
            model=settings.llm_model,
            temperature=0.1,
            max_tokens=256,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_user_prompt(symbol, trade_date, raw_text),
                },
            ],
        )

        content = response.choices[0].message.content.strip()
        raw_dict = json.loads(content)
        result = _validate_and_fix(raw_dict, symbol)

        logger.info(
            "Classified %s [%s]: type=%s sentiment=%s conf=%.2f",
            symbol, trade_date[:10], result["event_type"], result["sentiment"], result["confidence"],
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(
            "JSON parse error for %s on %s: %s — falling back to GENERAL_NEWS",
            symbol, trade_date, e,
        )
        return {
            "event_type": "GENERAL_NEWS",
            "sentiment": "NEUTRAL",
            "confidence": 0.3,
            "label": raw_text.strip()[:120],
        }

    except Exception as e:  # noqa: BLE001
        logger.error(
            "LLM call failed for %s on %s: %s — falling back to GENERAL_NEWS",
            symbol, trade_date, e,
        )
        return {
            "event_type": "GENERAL_NEWS",
            "sentiment": "NEUTRAL",
            "confidence": 0.3,
            "label": raw_text.strip()[:120],
        }
