"""
app/routers/collectors.py

FastAPI endpoints that trigger the Milestone 2 data collectors.

All endpoints are POST so they can be triggered manually from the Swagger UI
(at http://localhost:8000/docs) or via curl.

Endpoints:
    POST /collectors/load-nifty500
    POST /collectors/load-custom-watchlist
    POST /collectors/run-rss
    POST /collectors/run-announcements
    POST /collectors/run-preopen

Universe parameter (query string):
    universe = "nifty_500" | "nifty_50" | "custom_watchlist" | "all"
    Default is "nifty_500" for all endpoints.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.collectors.universe import load_nifty500, load_custom_watchlist, get_universe_symbols
from app.collectors.nse_rss import fetch_nse_rss_feeds
from app.collectors.nse_announcements import fetch_nse_announcements
from app.collectors.preopen import fetch_preopen_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/collectors", tags=["Collectors"])

UniverseType = Literal["nifty_500", "nifty_50", "custom_watchlist", "all"]


# ── 1. Load Nifty 500 universe ─────────────────────────────────────────────────
@router.post("/load-nifty500", summary="Load / refresh Nifty 500 symbol universe")
def endpoint_load_nifty500(db: Session = Depends(get_db)):
    """
    Downloads the official Nifty 500 constituent CSV from NSE and upserts
    rows into the symbols table.  Sets in_nifty_500 = True for all members.

    Safe to run repeatedly — it only updates or inserts, never hard-deletes.
    """
    result = load_nifty500(db=db)
    return {"status": "ok", "result": result}


# ── 2. Load custom watchlist ───────────────────────────────────────────────────
@router.post("/load-custom-watchlist", summary="Load custom watchlist from data/custom_watchlist.csv")
def endpoint_load_custom_watchlist(
    csv_path: str = Query(default="data/custom_watchlist.csv"),
    db: Session = Depends(get_db),
):
    """
    Reads data/custom_watchlist.csv and marks matching symbols with
    is_custom_watchlist = True.  Clears the flag on all previous watchlist symbols first.
    """
    result = load_custom_watchlist(csv_path=csv_path, db=db)
    return {"status": "ok", "result": result}


# ── 3. List universe symbols ───────────────────────────────────────────────────
@router.get("/universe", summary="List symbols for a universe filter")
def endpoint_universe(
    universe: UniverseType = Query(default="nifty_500"),
    db: Session = Depends(get_db),
):
    """
    Returns the list of symbols currently matching the chosen universe filter.
    Useful for verifying the universe before running collectors.
    """
    symbols = get_universe_symbols(universe=universe, db=db)
    return {"universe": universe, "count": len(symbols), "symbols": symbols}


# ── 4. NSE RSS collector ───────────────────────────────────────────────────────
@router.post("/run-rss", summary="Run NSE RSS feed collector")
def endpoint_run_rss(
    universe: UniverseType = Query(default="nifty_500"),
    db: Session = Depends(get_db),
):
    """
    Fetches NSE RSS feeds, matches items to universe symbols, and inserts
    new events into the events table (deduplicates by source URL).
    """
    result = fetch_nse_rss_feeds(universe=universe, db=db)
    return {"status": "ok", "result": result}


# ── 5. NSE announcements collector ────────────────────────────────────────────
@router.post("/run-announcements", summary="Run NSE corporate announcements collector")
def endpoint_run_announcements(
    since_hours: int = Query(default=24, ge=1, le=168),
    universe: UniverseType = Query(default="nifty_500"),
    db: Session = Depends(get_db),
):
    """
    Fetches corporate announcements from the NSE API for the last N hours,
    filters to the chosen universe, and stores new rows in the events table.
    """
    result = fetch_nse_announcements(since_hours=since_hours, universe=universe, db=db)
    return {"status": "ok", "result": result}


# ── 6. Pre-open collector ──────────────────────────────────────────────────────
@router.post("/run-preopen", summary="Run pre-open market data collector")
def endpoint_run_preopen(
    universe:  UniverseType = Query(default="nifty_500"),
    test_mode: bool         = Query(default=False),
    db: Session = Depends(get_db),
):
    """
    Fetches a single pre-open market data snapshot from NSE and stores it
    in preopen_snapshots.

    Set test_mode=true to run outside 9:00–9:15 am IST (uses mock data if
    the live NSE endpoint is not reachable).
    """
    result = fetch_preopen_snapshot(universe=universe, test_mode=test_mode, db=db)
    return {"status": "ok", "result": result}
