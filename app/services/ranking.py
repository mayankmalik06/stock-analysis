"""
app/services/ranking.py

Full scoring and ranking pipeline for the Nifty Pre-Market Briefing system.

MILESTONE: 3 (Scoring and Ranking)
STATUS: Implemented

This module:
1. Pulls active symbols from the database (filtered by universe flag).
2. For each symbol, pulls events in the configured time window.
3. For each symbol, pulls pre-open snapshots for the given session date.
4. Calls the four component scorers from services/scoring.py.
5. Computes the composite total_score.
6. Ranks all symbols by total_score (highest = rank 1).
7. Assigns watchlist buckets (A / B / C).
8. Writes rows to daily_rankings (upserts by trade_date + symbol).
9. Returns the full ranked list as a list of dicts for the API response.

Universe filters:
    "nifty_500"        → in_nifty_500 = True
    "nifty_50"         → in_nifty_50 = True
    "custom_watchlist" → is_custom_watchlist = True
    "all"              → all active symbols

Configuration:
    event_window_hours: how far back to look for events (default 24h)
    These defaults can be overridden per call without touching this file.
"""

import datetime
import logging
from typing import Literal, Optional

from sqlalchemy.orm import Session
from sqlalchemy import delete

from app.models import Symbol, Event, PreopenSnapshot, DailyRanking
from app.services.scoring import (
    compute_catalyst_score,
    compute_preopen_score,
    compute_liquidity_score,
    compute_technical_score,
    compute_total_score,
    assign_bucket,
    CatalystDetail,
)

logger = logging.getLogger(__name__)

UniverseType = Literal["nifty_500", "nifty_50", "custom_watchlist", "all"]


def _get_universe_symbols(universe: UniverseType, db: Session) -> list[Symbol]:
    """Return all active Symbol rows matching the requested universe filter."""
    query = db.query(Symbol).filter(Symbol.is_active == True)  # noqa: E712

    if universe == "nifty_500":
        query = query.filter(Symbol.in_nifty_500 == True)  # noqa: E712
    elif universe == "nifty_50":
        query = query.filter(Symbol.in_nifty_50 == True)  # noqa: E712
    elif universe == "custom_watchlist":
        query = query.filter(Symbol.is_custom_watchlist == True)  # noqa: E712
    # "all" → no extra filter

    return query.all()


def _get_events_for_symbol(
    symbol: str,
    db: Session,
    since: datetime.datetime,
) -> list[dict]:
    """
    Return all events for a symbol ingested after `since`.
    Returns a list of plain dicts (easier to pass around than ORM objects).
    """
    rows = (
        db.query(Event)
        .filter(Event.symbol == symbol)
        .filter(Event.ingested_at >= since)
        .order_by(Event.event_timestamp.asc())
        .all()
    )

    return [
        {
            "event_id": r.event_id,
            "symbol": r.symbol,
            "source": r.source or "",
            "headline": r.headline or "",
            "category": r.category,
            "sentiment": r.sentiment,
            "priority_label": r.priority_label,
            "event_timestamp": r.event_timestamp,
        }
        for r in rows
    ]


def _get_snapshots_for_symbol(
    symbol: str,
    db: Session,
    trade_date: datetime.date,
) -> list[dict]:
    """
    Return pre-open snapshots for a symbol on the given trade_date.
    Snapshots are identified by snapshot_time falling on that calendar date.
    Returns a list of plain dicts sorted by snapshot_time ascending.
    """
    date_start = datetime.datetime.combine(trade_date, datetime.time.min)
    date_end = datetime.datetime.combine(trade_date, datetime.time.max)

    rows = (
        db.query(PreopenSnapshot)
        .filter(PreopenSnapshot.symbol == symbol)
        .filter(PreopenSnapshot.snapshot_time >= date_start)
        .filter(PreopenSnapshot.snapshot_time <= date_end)
        .order_by(PreopenSnapshot.snapshot_time.asc())
        .all()
    )

    return [
        {
            "snapshot_id": r.snapshot_id,
            "symbol": r.symbol,
            "snapshot_time": r.snapshot_time,
            "prev_close": r.prev_close,
            "indicative_price": r.indicative_price,
            "gap_pct": r.gap_pct,
            "buy_qty": r.buy_qty,
            "sell_qty": r.sell_qty,
            "indicative_volume": r.indicative_volume,
            "indicative_value": r.indicative_value,
        }
        for r in rows
    ]


def _upsert_daily_ranking(
    db: Session,
    trade_date: datetime.date,
    symbol: str,
    catalyst_score: float,
    preopen_score: float,
    liquidity_score: float,
    technical_score: float,
    total_score: float,
    rank: int,
    watchlist_bucket: str,
) -> DailyRanking:
    """
    Insert or update a DailyRanking row for (trade_date, symbol).
    If a row already exists for this date+symbol, it is overwritten.
    """
    existing = (
        db.query(DailyRanking)
        .filter(DailyRanking.trade_date == trade_date)
        .filter(DailyRanking.symbol == symbol)
        .first()
    )

    if existing:
        existing.catalyst_score = catalyst_score
        existing.preopen_score = preopen_score
        existing.liquidity_score = liquidity_score
        existing.technical_score = technical_score
        existing.total_score = total_score
        existing.rank = rank
        existing.watchlist_bucket = watchlist_bucket
        return existing
    else:
        row = DailyRanking(
            trade_date=trade_date,
            symbol=symbol,
            catalyst_score=catalyst_score,
            preopen_score=preopen_score,
            liquidity_score=liquidity_score,
            technical_score=technical_score,
            total_score=total_score,
            rank=rank,
            watchlist_bucket=watchlist_bucket,
        )
        db.add(row)
        return row


def run_scoring(
    db: Session,
    trade_date: Optional[datetime.date] = None,
    universe: UniverseType = "nifty_500",
    event_window_hours: int = 24,
) -> dict:
    """
    Main scoring pipeline. Call this to compute and save rankings for a date.

    Steps:
      1. Resolve trade_date (default = today).
      2. Load active symbols for the chosen universe.
      3. For each symbol: pull events + snapshots, compute 4 scores.
      4. Sort all symbols by total_score descending.
      5. Assign rank integers (1 = highest score).
      6. Assign watchlist buckets.
      7. Upsert all rows into daily_rankings.
      8. Commit the transaction.

    Returns a summary dict with counts and the full ranked list.
    """
    if trade_date is None:
        trade_date = datetime.date.today()

    event_cutoff = datetime.datetime.now() - datetime.timedelta(hours=event_window_hours)

    logger.info(
        "Starting scoring run | date=%s | universe=%s | event_window=%dh",
        trade_date, universe, event_window_hours,
    )

    # ── 1. Load universe ─────────────────────────────────────────────
    symbols = _get_universe_symbols(universe=universe, db=db)
    if not symbols:
        logger.warning("No symbols found for universe=%s. Scoring skipped.", universe)
        return {
            "trade_date": str(trade_date),
            "universe": universe,
            "symbols_scored": 0,
            "message": f"No symbols found for universe '{universe}'. Load symbols first.",
            "results": [],
        }

    logger.info("Universe loaded: %d symbols.", len(symbols))

    # ── 2. Score each symbol ─────────────────────────────────────────
    scored_rows = []

    for sym in symbols:
        ticker = sym.symbol

        # Pull raw data for this symbol
        events = _get_events_for_symbol(ticker, db, since=event_cutoff)
        snapshots = _get_snapshots_for_symbol(ticker, db, trade_date=trade_date)

        # Compute component scores
        catalyst_score, catalyst_detail = compute_catalyst_score(
            events=events,
            window_hours=event_window_hours,
        )

        preopen_score = compute_preopen_score(snapshots=snapshots)

        liquidity_score = compute_liquidity_score(
            symbol_data={
                "avg_daily_value_20d": sym.avg_daily_value_20d,
                "is_fno": sym.is_fno,
            }
        )

        technical_score = compute_technical_score(snapshots=snapshots)

        total_score = compute_total_score(
            catalyst=catalyst_score,
            preopen=preopen_score,
            liquidity=liquidity_score,
            technical=technical_score,
        )

        scored_rows.append({
            "symbol": ticker,
            "company_name": sym.company_name,
            "catalyst_score": catalyst_score,
            "preopen_score": preopen_score,
            "liquidity_score": liquidity_score,
            "technical_score": technical_score,
            "total_score": total_score,
            # rank and bucket assigned after sort below
            "rank": None,
            "watchlist_bucket": None,
            # extra context for logging / debugging
            "_event_count": catalyst_detail.event_count,
            "_best_impact": catalyst_detail.best_impact,
            "_snapshot_count": len(snapshots),
        })

    # ── 3. Sort and assign ranks ──────────────────────────────────────
    scored_rows.sort(key=lambda r: r["total_score"], reverse=True)

    for rank_num, row in enumerate(scored_rows, start=1):
        row["rank"] = rank_num
        row["watchlist_bucket"] = assign_bucket(row["total_score"])

    # ── 4. Write to database ──────────────────────────────────────────
    for row in scored_rows:
        _upsert_daily_ranking(
            db=db,
            trade_date=trade_date,
            symbol=row["symbol"],
            catalyst_score=row["catalyst_score"],
            preopen_score=row["preopen_score"],
            liquidity_score=row["liquidity_score"],
            technical_score=row["technical_score"],
            total_score=row["total_score"],
            rank=row["rank"],
            watchlist_bucket=row["watchlist_bucket"],
        )

    db.commit()

    # ── 5. Summary ────────────────────────────────────────────────────
    a_count = sum(1 for r in scored_rows if r["watchlist_bucket"] == "A")
    b_count = sum(1 for r in scored_rows if r["watchlist_bucket"] == "B")
    c_count = sum(1 for r in scored_rows if r["watchlist_bucket"] == "C")

    logger.info(
        "Scoring complete | %d symbols | A=%d B=%d C=%d",
        len(scored_rows), a_count, b_count, c_count,
    )

    return {
        "trade_date": str(trade_date),
        "universe": universe,
        "event_window_hours": event_window_hours,
        "symbols_scored": len(scored_rows),
        "bucket_counts": {"A": a_count, "B": b_count, "C": c_count},
        "results": [
            {
                "rank": r["rank"],
                "symbol": r["symbol"],
                "company_name": r["company_name"],
                "catalyst_score": r["catalyst_score"],
                "preopen_score": r["preopen_score"],
                "liquidity_score": r["liquidity_score"],
                "technical_score": r["technical_score"],
                "total_score": r["total_score"],
                "watchlist_bucket": r["watchlist_bucket"],
                "event_count": r["_event_count"],
                "best_catalyst_impact": r["_best_impact"],
                "snapshot_count": r["_snapshot_count"],
            }
            for r in scored_rows
        ],
    }


def get_top_rankings(
    db: Session,
    trade_date: datetime.date,
    limit: int = 20,
    bucket: Optional[str] = None,
) -> list[dict]:
    """
    Retrieve the top N ranked symbols for a given trade_date from daily_rankings.

    Optionally filter by watchlist_bucket ("A", "B", or "C").
    Returns results sorted by rank ascending (rank 1 = best).
    """
    query = (
        db.query(DailyRanking)
        .filter(DailyRanking.trade_date == trade_date)
    )

    if bucket:
        query = query.filter(DailyRanking.watchlist_bucket == bucket.upper())

    rows = query.order_by(DailyRanking.rank.asc()).limit(limit).all()

    return [
        {
            "rank": r.rank,
            "symbol": r.symbol,
            "trade_date": str(r.trade_date),
            "catalyst_score": r.catalyst_score,
            "preopen_score": r.preopen_score,
            "liquidity_score": r.liquidity_score,
            "technical_score": r.technical_score,
            "total_score": r.total_score,
            "watchlist_bucket": r.watchlist_bucket,
        }
        for r in rows
    ]
