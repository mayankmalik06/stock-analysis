"""
app/routers/scoring.py

FastAPI endpoints for triggering the Milestone 3 scoring pipeline
and querying the results from daily_rankings.

MILESTONE: 3 (Scoring and Ranking)

Endpoints:
    POST /scoring/run
        Trigger a full scoring run for a given trade date and universe.
        Writes (or overwrites) rows in daily_rankings.
        Returns summary and full ranked list.

    GET  /scoring/top
        List the top N ranked symbols for a given trade date.
        Optionally filter by watchlist_bucket (A, B, or C).
"""

import datetime
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.ranking import run_scoring, get_top_rankings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scoring", tags=["Scoring"])

UniverseType = Literal["nifty_500", "nifty_50", "custom_watchlist", "all"]


# ── POST /scoring/run ──────────────────────────────────────────────────────────
@router.post("/run", summary="Run scoring for a trade date")
def endpoint_run_scoring(
    trade_date: Optional[str] = Query(
        default=None,
        description="Trade date as YYYY-MM-DD. Defaults to today.",
    ),
    universe: UniverseType = Query(
        default="nifty_500",
        description="Which universe to score: nifty_500 | nifty_50 | custom_watchlist | all",
    ),
    event_window_hours: int = Query(
        default=24,
        ge=1,
        le=168,
        description="How many hours of events to consider for catalyst scoring.",
    ),
    db: Session = Depends(get_db),
):
    """
    Runs the full scoring pipeline for a given trade date.

    1. Pulls events from the last `event_window_hours` hours.
    2. Pulls pre-open snapshots for the given date.
    3. Computes Catalyst, Pre-open, Liquidity, and Technical scores per symbol.
    4. Computes composite total_score = 0.40*Catalyst + 0.25*Preopen + 0.20*Liquidity + 0.15*Technical.
    5. Ranks symbols by total_score (rank 1 = highest).
    6. Assigns watchlist_bucket: A (>=70), B (50–69), C (<50).
    7. Writes results to daily_rankings.

    Safe to run multiple times — existing rows for the same date are overwritten.
    """
    # Parse date or default to today
    if trade_date:
        try:
            parsed_date = datetime.date.fromisoformat(trade_date)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format: '{trade_date}'. Use YYYY-MM-DD.",
            )
    else:
        parsed_date = datetime.date.today()

    logger.info("Scoring run requested | date=%s | universe=%s", parsed_date, universe)

    result = run_scoring(
        db=db,
        trade_date=parsed_date,
        universe=universe,
        event_window_hours=event_window_hours,
    )

    return {"status": "ok", "result": result}


# ── GET /scoring/top ───────────────────────────────────────────────────────────
@router.get("/top", summary="List top ranked symbols for a trade date")
def endpoint_get_top(
    date: Optional[str] = Query(
        default=None,
        description="Trade date as YYYY-MM-DD. Defaults to today.",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=500,
        description="Maximum number of symbols to return.",
    ),
    bucket: Optional[str] = Query(
        default=None,
        description="Filter by watchlist bucket: A, B, or C. Leave blank for all.",
    ),
    db: Session = Depends(get_db),
):
    """
    Returns the top N ranked symbols for a given trade date from daily_rankings.

    Use `bucket=A` to see only the highest-priority names.
    Use `bucket=B` for secondary watch candidates.

    Results are sorted by rank ascending (rank 1 = best score).

    Run /scoring/run first to populate daily_rankings for the date.
    """
    if date:
        try:
            parsed_date = datetime.date.fromisoformat(date)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format: '{date}'. Use YYYY-MM-DD.",
            )
    else:
        parsed_date = datetime.date.today()

    if bucket and bucket.upper() not in ("A", "B", "C"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid bucket '{bucket}'. Use A, B, or C.",
        )

    rows = get_top_rankings(
        db=db,
        trade_date=parsed_date,
        limit=limit,
        bucket=bucket,
    )

    return {
        "trade_date": str(parsed_date),
        "bucket_filter": bucket,
        "count": len(rows),
        "rankings": rows,
    }
