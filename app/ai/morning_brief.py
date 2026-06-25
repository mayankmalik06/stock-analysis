"""
app/ai/morning_brief.py

Morning brief generator for the Nifty Pre-Market Briefing system.

MILESTONE: 4 (AI Layer)
STATUS: Implemented

Responsibility:
    Reads ranked symbols + classified events for a trade_date.
    Produces:
      1. A structured JSON representation of the brief
         (top board, catalysts, risks, noisy-but-interesting)
      2. A natural-language Markdown brief the user can read directly
         (3–6 paragraphs + bullet lists, pre-market briefing style)

Rules:
    - AI explains the ranked list. AI never produces the ranked list.
    - All inputs are structured (JSON) and come from the database.
    - Temperature 0.2–0.3 for consistency.
    - If LLM_API_KEY is missing → dry-run mode returns a mock brief.
    - Never call external websites from inside this module.

Usage:
    from app.ai.morning_brief import generate_brief

    result = generate_brief(db=db, trade_date=date(2026, 6, 25))
    # result = {
    #     "trade_date": "2026-06-25",
    #     "top_symbols": [...],
    #     "sections": {...},
    #     "rendered_brief": "## Pre-Market Brief — 25 Jun 2026\n...",
    # }
"""

import json
import logging
import datetime
from typing import Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import settings
from app.models import DailyRanking, SymbolEvent, Symbol

logger = logging.getLogger(__name__)


# ── System prompt for brief generation ───────────────────────────────────────
BRIEF_SYSTEM_PROMPT = """You are an expert Indian stock market analyst writing a pre-market morning briefing for an intraday trader.

You will receive a JSON input containing:
  - trade_date: the date of the session
  - top_symbols: top-ranked stocks with their scores and event tags
  - positive_catalysts: stocks with positive events driving them higher
  - risk_names: stocks with negative or risk events
  - noisy_items: stocks that are interesting but not top-tier

Your task is to write a concise, professional pre-market briefing in Markdown format.

STRICT RULES:
1. Write 3 to 6 paragraphs plus bullet lists where helpful.
2. The brief must be readable in under 2 minutes.
3. Do NOT invent facts, prices, or catalysts not present in the input data. Use the actual headline or label text from event_tags to describe why a stock matters today — quote key phrases directly.
4. Focus only on what is actionable for an intraday trader.
5. Use short, direct sentences — this is a briefing, not a research report.
6. Mention specific score ranges only where they clarify context (e.g. "A-grade catalyst").
7. Always include: market context paragraph, top watchlist bullets, risk/caution flags.
8. End with a one-line "Watch for" sentence summarising the session theme.
9. Use Markdown headers (##, ###) for sections.
10. Keep the total output under 600 words.

Output format:
## Pre-Market Brief — {date}

### Market Context
[1-2 sentences on overall tone from events]

### Top Watchlist
[bullet list: Symbol — why it matters today — key level/action note]

### Secondary Watch
[bullet list: Symbol — one-liner]

### Risk Flags
[bullet list: any names with negative catalysts, liquidity concerns, or gap-fill risk]

### Watch For
[one sentence: session theme for the day]"""


def _build_brief_input(
    ranked: list[dict],
    events_map: dict[str, list[dict]],
    trade_date: str,
) -> dict:
    """
    Builds the structured JSON input sent to the LLM brief writer.

    ranked      — list of scored symbol dicts from get_top_rankings()
    events_map  — dict of symbol → list of classified events
    trade_date  — ISO date string
    """
    top_symbols = []
    positive_catalysts = []
    risk_names = []
    noisy_items = []

    for r in ranked:
        symbol = r["symbol"]
        bucket = r.get("watchlist_bucket", "C")
        events = events_map.get(symbol, [])

        # Build event tags list — include headline and source for richer brief
        event_tags = [
            {
                "event_type": e.get("event_type", "GENERAL_NEWS"),
                "sentiment": e.get("sentiment", "NEUTRAL"),
                # Prefer the AI label; fall back to headline if label is empty
                "label": e.get("label") or e.get("headline", ""),
                "headline": e.get("headline", ""),
                "source": e.get("source", "SEED"),
            }
            for e in events
            if e.get("event_type") not in ("NO_EVENT", None)
        ]

        symbol_summary = {
            "rank": r.get("rank"),
            "symbol": symbol,
            "bucket": bucket,
            "total_score": r.get("total_score"),
            "catalyst_score": r.get("catalyst_score"),
            "preopen_score": r.get("preopen_score"),
            "liquidity_score": r.get("liquidity_score"),
            "technical_score": r.get("technical_score"),
            "event_tags": event_tags,
        }

        # Route to sections
        if bucket == "A":
            top_symbols.append(symbol_summary)

            # Separate positive vs risk based on dominant event sentiment
            sentiments = [e.get("sentiment") for e in events if e.get("event_type") not in ("NO_EVENT", None)]
            has_risk = any(
                e.get("event_type") == "RISK"
                for e in events
            )
            has_positive = "POSITIVE" in sentiments

            if has_risk or (sentiments and all(s == "NEGATIVE" for s in sentiments)):
                risk_names.append(symbol_summary)
            elif has_positive:
                positive_catalysts.append(symbol_summary)

        elif bucket == "B":
            top_symbols.append(symbol_summary)
            noisy_items.append(symbol_summary)

    return {
        "trade_date": trade_date,
        "top_symbols": top_symbols[:10],          # cap at 10 for prompt length
        "positive_catalysts": positive_catalysts[:5],
        "risk_names": risk_names[:5],
        "noisy_items": noisy_items[:5],
    }


def _mock_brief(brief_input: dict) -> str:
    """
    Generates a simple deterministic mock brief when no LLM API key is configured.
    Used for offline testing and CI environments.
    """
    trade_date = brief_input.get("trade_date", "Unknown Date")
    top_symbols = brief_input.get("top_symbols", [])
    risk_names = brief_input.get("risk_names", [])

    lines = [
        f"## Pre-Market Brief — {trade_date} [MOCK]",
        "",
        "### Market Context",
        "Multiple catalyst-driven names visible this morning. Overall tone is cautiously positive with select earnings and rating-driven movers.",
        "",
        "### Top Watchlist",
    ]

    for s in top_symbols[:5]:
        tags = ", ".join(t["event_type"] for t in s.get("event_tags", [])[:2]) or "No catalyst"
        lines.append(
            f"- **{s['symbol']}** — Score {s['total_score']} ({s['bucket']}-grade) | {tags}"
        )

    lines += ["", "### Secondary Watch"]
    for s in top_symbols[5:10]:
        lines.append(f"- **{s['symbol']}** — Watch for follow-through | Bucket {s['bucket']}")

    lines += ["", "### Risk Flags"]
    if risk_names:
        for s in risk_names:
            lines.append(f"- **{s['symbol']}** — Negative event flagged; trade with caution")
    else:
        lines.append("- No major risk flags identified today")

    lines += [
        "",
        "### Watch For",
        "Earnings-driven momentum names in the first 15 minutes; avoid chasing gap-downs without confirmation.",
        "",
        "> _This is a mock brief generated in dry-run mode. Connect LLM_API_KEY for AI-generated text._",
    ]

    return "\n".join(lines)


# ── Main generator function ───────────────────────────────────────────────────
def generate_brief(
    db: Session,
    trade_date: datetime.date,
    top_n: int = 20,
) -> dict:
    """
    Generate the morning brief for a given trade_date.

    Parameters
    ----------
    db          : SQLAlchemy session
    trade_date  : date to generate the brief for
    top_n       : how many top-ranked symbols to pull (default 20)

    Returns
    -------
    dict with keys:
        trade_date      (str)           — ISO date string
        top_symbols     (list[dict])    — ranked board with scores + event tags
        sections        (dict)          — structured brief sections
        rendered_brief  (str)           — final Markdown text

    Raises
    -------
    ValueError  — if no rankings exist for the given date
    """
    date_str = str(trade_date)

    # ── 1. Load rankings ──────────────────────────────────────────────────
    ranking_rows = (
        db.query(DailyRanking)
        .filter(DailyRanking.trade_date == trade_date)
        .order_by(DailyRanking.rank.asc())
        .limit(top_n)
        .all()
    )

    if not ranking_rows:
        raise ValueError(
            f"No rankings found for {date_str}. "
            "Run scoring first: python scripts/run_scoring.py --date {date_str}"
        )

    ranked = [
        {
            "rank": r.rank,
            "symbol": r.symbol,
            "watchlist_bucket": r.watchlist_bucket,
            "total_score": r.total_score,
            "catalyst_score": r.catalyst_score,
            "preopen_score": r.preopen_score,
            "liquidity_score": r.liquidity_score,
            "technical_score": r.technical_score,
        }
        for r in ranking_rows
    ]

    symbols_in_ranked = [r["symbol"] for r in ranked]

    # ── 2. Load classified events for those symbols ───────────────────────
    event_rows = (
        db.query(SymbolEvent)
        .filter(SymbolEvent.trade_date == trade_date)
        .filter(SymbolEvent.symbol.in_(symbols_in_ranked))
        .all()
    )

    events_map: dict[str, list[dict]] = {}
    for row in event_rows:
        events_map.setdefault(row.symbol, []).append(
            {
                "event_type": row.event_type,
                "sentiment": row.sentiment,
                "confidence": row.confidence,
                "label": row.label,
                "raw_text": row.raw_text,
                # Milestone 6: provenance for richer brief text
                "headline": row.headline or "",
                "source": row.source or "SEED",
            }
        )

    # ── 3. Build structured JSON input ────────────────────────────────────
    brief_input = _build_brief_input(ranked, events_map, date_str)

    # Top 5 for API response (A-grade + top B-grade)
    top_5 = ranked[:5]
    top_symbols_response = []
    for r in top_5:
        sym = r["symbol"]
        event_tags = [
            {
                "event_type": e.get("event_type"),
                "sentiment": e.get("sentiment"),
                "label": e.get("label", ""),
            }
            for e in events_map.get(sym, [])
            if e.get("event_type") not in ("NO_EVENT", None)
        ]
        top_symbols_response.append({
            "rank": r["rank"],
            "symbol": sym,
            "bucket": r["watchlist_bucket"],
            "total_score": r["total_score"],
            "catalyst_score": r["catalyst_score"],
            "preopen_score": r["preopen_score"],
            "liquidity_score": r["liquidity_score"],
            "technical_score": r["technical_score"],
            "event_tags": event_tags,
        })

    sections = {
        "positive_catalysts": [s["symbol"] for s in brief_input["positive_catalysts"]],
        "risk_names": [s["symbol"] for s in brief_input["risk_names"]],
        "noisy_items": [s["symbol"] for s in brief_input["noisy_items"]],
        "top_board": [s["symbol"] for s in brief_input["top_symbols"]],
    }

    # ── 4. Generate natural-language brief ───────────────────────────────
    if not settings.llm_api_key:
        logger.info("LLM_API_KEY not set — using mock brief generator for %s.", date_str)
        rendered_brief = _mock_brief(brief_input)
    else:
        rendered_brief = _call_llm_for_brief(brief_input)

    return {
        "trade_date": date_str,
        "top_symbols": top_symbols_response,
        "sections": sections,
        "rendered_brief": rendered_brief,
    }


def _call_llm_for_brief(brief_input: dict) -> str:
    """
    Call the LLM to generate the natural-language morning brief.
    Falls back to mock on any error.
    """
    try:
        client = OpenAI(api_key=settings.llm_api_key)

        user_content = (
            "Here is today's pre-market data. Please write the morning briefing.\n\n"
            + json.dumps(brief_input, indent=2)
        )

        response = client.chat.completions.create(
            model=settings.llm_model,
            temperature=0.25,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )

        brief_text = response.choices[0].message.content.strip()
        logger.info(
            "Morning brief generated via LLM for %s (%d chars)",
            brief_input.get("trade_date"), len(brief_text),
        )
        return brief_text

    except Exception as e:  # noqa: BLE001
        logger.error("LLM brief generation failed: %s — using mock brief.", e)
        return _mock_brief(brief_input)
