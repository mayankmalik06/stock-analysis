"""
app/routers/brief.py

FastAPI router for the morning brief endpoints.

Milestone: 4 (AI Layer)

Endpoints:
    GET /brief/{trade_date}
        Returns the morning brief for a given date as structured JSON + rendered text.
        Returns 404 if no rankings exist for the date.
        Returns 422 if the date format is invalid.

    GET /brief/{trade_date}/events
        Returns all AI-classified events for the symbols in that day's brief.
        Useful for auditing classifier outputs.
"""

import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.ai.morning_brief import generate_brief
from app.models import SymbolEvent, DailyRanking
from app.schemas import MorningBriefResponse, BriefSections, TopSymbol, EventTag, SymbolEventRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brief", tags=["Brief"])


@router.get(
    "/{trade_date}",
    response_model=MorningBriefResponse,
    summary="Get the morning brief for a trade date",
    description=(
        "Returns the AI-generated morning brief for the given trade date. "
        "Requires that scoring (run_scoring.py) and event classification "
        "(classify_events.py) have already been run for this date. "
        "Returns 404 if no rankings are found."
    ),
)
def get_morning_brief(
    trade_date: datetime.date,
    db: Session = Depends(get_db),
):
    """
    Generate and return the morning brief for trade_date.

    Path parameter:
        trade_date — ISO date string, e.g. 2026-06-25

    Returns:
        MorningBriefResponse with top_symbols, sections, rendered_brief
    """
    try:
        result = generate_brief(db=db, trade_date=trade_date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error generating brief for %s", trade_date)
        raise HTTPException(
            status_code=500,
            detail=f"Brief generation failed: {e}",
        )

    # Convert raw dicts to Pydantic models
    top_symbols = [
        TopSymbol(
            rank=s["rank"],
            symbol=s["symbol"],
            bucket=s["bucket"],
            total_score=s.get("total_score"),
            catalyst_score=s.get("catalyst_score"),
            preopen_score=s.get("preopen_score"),
            liquidity_score=s.get("liquidity_score"),
            technical_score=s.get("technical_score"),
            event_tags=[
                EventTag(
                    event_type=t["event_type"],
                    sentiment=t["sentiment"],
                    label=t.get("label", ""),
                )
                for t in s.get("event_tags", [])
            ],
        )
        for s in result["top_symbols"]
    ]

    raw_sections = result.get("sections", {})
    sections = BriefSections(
        top_board=raw_sections.get("top_board", []),
        positive_catalysts=raw_sections.get("positive_catalysts", []),
        risk_names=raw_sections.get("risk_names", []),
        noisy_items=raw_sections.get("noisy_items", []),
    )

    return MorningBriefResponse(
        trade_date=trade_date,
        top_symbols=top_symbols,
        sections=sections,
        rendered_brief=result["rendered_brief"],
    )


@router.get(
    "/{trade_date}/events",
    response_model=list[SymbolEventRead],
    summary="List AI-classified events for a trade date",
    description=(
        "Returns all AI-classified symbol_events rows for the trade date. "
        "Useful for auditing classifier outputs and verifying event labelling. "
        "Returns 404 if no events are found."
    ),
)
def get_brief_events(
    trade_date: datetime.date,
    db: Session = Depends(get_db),
):
    """
    Return all classified events for trade_date.
    Optionally scoped to symbols that appear in the rankings.
    """
    # Get symbols that have rankings on this date
    ranked_symbols = [
        r.symbol
        for r in db.query(DailyRanking.symbol)
        .filter(DailyRanking.trade_date == trade_date)
        .all()
    ]

    if not ranked_symbols:
        raise HTTPException(
            status_code=404,
            detail=f"No rankings found for {trade_date}. Run scoring first.",
        )

    events = (
        db.query(SymbolEvent)
        .filter(SymbolEvent.trade_date == trade_date)
        .filter(SymbolEvent.symbol.in_(ranked_symbols))
        .order_by(SymbolEvent.symbol.asc(), SymbolEvent.created_at.asc())
        .all()
    )

    if not events:
        raise HTTPException(
            status_code=404,
            detail=f"No classified events found for {trade_date}. Run classify_events.py first.",
        )

    return events
