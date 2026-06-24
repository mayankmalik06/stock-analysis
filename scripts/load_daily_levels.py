#!/usr/bin/env python3
"""
scripts/load_daily_levels.py

Milestone 3.5 — CLI script to load previous-day OHLC levels into daily_levels.

For a given trade_date (e.g. 2026-06-25), this script fetches or approximates
the previous trading session's high, low, and close for all active symbols in
the chosen universe, then writes them into the daily_levels table.

Usage examples:
    # Load levels for a specific date (most common usage)
    python scripts/load_daily_levels.py --date 2026-06-25

    # Load for today
    python scripts/load_daily_levels.py

    # Seed deterministic test data (no network call — safe for local testing)
    python scripts/load_daily_levels.py --date 2026-06-25 --mode seed

    # Load levels for Nifty 50 universe only
    python scripts/load_daily_levels.py --date 2026-06-25 --universe nifty_50

    # Load levels for custom watchlist only
    python scripts/load_daily_levels.py --date 2026-06-25 --universe custom_watchlist

Run from the project root:
    python scripts/load_daily_levels.py --date 2026-06-25

------------------------------------------------------------
Data source note (Phase 1)
------------------------------------------------------------
NSE does not provide a simple public REST endpoint for historical OHLC.
The pre-open collector already stores prev_close in preopen_snapshots.

Phase 1 approach:
  - Mode "preopen" (default): derive prev_high/low from existing preopen_snapshots
    using prev_close as a proxy. We estimate a plausible high/low range using
    a fixed percentage band around prev_close (e.g. +/-1.5% as a conservative
    stand-in for the actual day's range).
    This is a deliberate Phase 1 approximation.
    TODO: Replace with real NSE EOD OHLC data in Phase 2.

  - Mode "seed": inserts deterministic test rows using a simple formula.
    Useful for running tests and demos without network access.

  In both cases, the source column in daily_levels records exactly what was used.

------------------------------------------------------------
Upsert behaviour
------------------------------------------------------------
If a row already exists for (trade_date, symbol), it is overwritten.
This means re-running the script for the same date is safe.
"""

import argparse
import datetime
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db import SessionLocal, create_tables
from app.models import Symbol, PreopenSnapshot, DailyLevel

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Load previous-day OHLC levels into daily_levels table."
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Trade date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--universe",
        type=str,
        default="all",
        choices=["nifty_500", "nifty_50", "custom_watchlist", "all"],
        help="Symbol universe to load levels for. Default: all active symbols.",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="preopen",
        choices=["preopen", "seed"],
        help=(
            "Data source mode.\n"
            "  preopen: derive from existing preopen_snapshots (default).\n"
            "  seed:    insert deterministic test data — no network call."
        ),
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Universe filter
# ---------------------------------------------------------------------------

def get_universe_symbols(universe: str, db) -> list[Symbol]:
    """Return active Symbol rows filtered by universe flag."""
    query = db.query(Symbol).filter(Symbol.is_active == True)  # noqa: E712

    if universe == "nifty_500":
        query = query.filter(Symbol.in_nifty_500 == True)  # noqa: E712
    elif universe == "nifty_50":
        query = query.filter(Symbol.in_nifty_50 == True)  # noqa: E712
    elif universe == "custom_watchlist":
        query = query.filter(Symbol.is_custom_watchlist == True)  # noqa: E712
    # "all" -> no extra filter

    return query.all()


# ---------------------------------------------------------------------------
# Mode: derive from preopen_snapshots
# ---------------------------------------------------------------------------

def load_from_preopen(db, symbols: list[Symbol], trade_date: datetime.date) -> list[dict]:
    """
    Derive prev_high and prev_low from existing preopen_snapshots.

    The pre-open collector stores prev_close for each snapshot.
    Since we do not yet have a real EOD OHLC feed, we estimate the
    previous day's trading range using a fixed percentage band around
    the prev_close.

    Estimation formula:
        prev_high  = prev_close * 1.015   (i.e. prev_close + 1.5%)
        prev_low   = prev_close * 0.985   (i.e. prev_close - 1.5%)

    This is a conservative approximation. A typical Nifty 500 stock
    moves roughly 1-3% intraday, so 1.5% captures a plausible range.

    TODO (Phase 2): Replace this with real NSE EOD bhavcopy data.
    The NSE bhavcopy CSV is publicly available at:
        https://archives.nseindia.com/content/historical/EQUITIES/...
    Implementing this download is deferred to Phase 2.

    Returns a list of level dicts ready to upsert.
    """
    levels = []

    for sym in symbols:
        ticker = sym.symbol

        # Find most recent preopen snapshot for this symbol on trade_date
        # (the prev_close stored there is the previous session's close)
        date_start = datetime.datetime.combine(trade_date, datetime.time.min)
        date_end = datetime.datetime.combine(trade_date, datetime.time.max)

        snap = (
            db.query(PreopenSnapshot)
            .filter(PreopenSnapshot.symbol == ticker)
            .filter(PreopenSnapshot.snapshot_time >= date_start)
            .filter(PreopenSnapshot.snapshot_time <= date_end)
            .order_by(PreopenSnapshot.snapshot_time.desc())
            .first()
        )

        if snap and snap.prev_close and snap.prev_close > 0:
            prev_close = snap.prev_close
            # Conservative range estimation — see docstring above
            prev_high = round(prev_close * 1.015, 2)
            prev_low = round(prev_close * 0.985, 2)
            levels.append({
                "trade_date": trade_date,
                "symbol": ticker,
                "prev_high": prev_high,
                "prev_low": prev_low,
                "prev_close": prev_close,
                "source": "PREOPEN_DERIVED",
            })
        else:
            logger.debug("No preopen snapshot for %s on %s — skipping.", ticker, trade_date)

    return levels


# ---------------------------------------------------------------------------
# Mode: seed deterministic test data
# ---------------------------------------------------------------------------

def load_seed_data(db, symbols: list[Symbol], trade_date: datetime.date) -> list[dict]:
    """
    Insert deterministic seed levels for testing and local demos.

    Uses a fixed formula based on the symbol name hash so that:
    - Each symbol always gets the same values for a given date.
    - Values are plausible (close around 500-2000, range ~2%).
    - No network call required.

    Source tag: "SEED"
    """
    levels = []

    for sym in symbols:
        ticker = sym.symbol
        # Deterministic base price from symbol hash
        base = 500 + (hash(ticker) % 1500)
        prev_close = round(float(base), 2)
        prev_high = round(prev_close * 1.018, 2)   # ~1.8% above close
        prev_low = round(prev_close * 0.982, 2)    # ~1.8% below close

        levels.append({
            "trade_date": trade_date,
            "symbol": ticker,
            "prev_high": prev_high,
            "prev_low": prev_low,
            "prev_close": prev_close,
            "source": "SEED",
        })

    return levels


# ---------------------------------------------------------------------------
# Upsert into daily_levels
# ---------------------------------------------------------------------------

def upsert_levels(db, levels: list[dict]) -> int:
    """
    Upsert a list of level dicts into daily_levels.
    Returns the count of rows written.

    Uses SQLite's INSERT OR REPLACE pattern via SQLAlchemy's
    sqlite_insert().on_conflict_do_update() to handle the
    unique constraint on (trade_date, symbol).
    """
    if not levels:
        return 0

    for lv in levels:
        stmt = (
            sqlite_insert(DailyLevel)
            .values(
                trade_date=lv["trade_date"],
                symbol=lv["symbol"],
                prev_high=lv["prev_high"],
                prev_low=lv["prev_low"],
                prev_close=lv["prev_close"],
                source=lv["source"],
            )
            .on_conflict_do_update(
                index_elements=["trade_date", "symbol"],
                set_={
                    "prev_high": lv["prev_high"],
                    "prev_low": lv["prev_low"],
                    "prev_close": lv["prev_close"],
                    "source": lv["source"],
                    "loaded_at": datetime.datetime.now(),
                },
            )
        )
        db.execute(stmt)

    db.commit()
    return len(levels)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # Resolve trade_date
    if args.date:
        try:
            trade_date = datetime.date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: Invalid date '{args.date}'. Use YYYY-MM-DD format.")
            sys.exit(1)
    else:
        trade_date = datetime.date.today()

    print(f"\n{'='*60}")
    print(f"  Milestone 3.5 — Load Daily Levels")
    print(f"{'='*60}")
    print(f"  Trade date : {trade_date}")
    print(f"  Universe   : {args.universe}")
    print(f"  Mode       : {args.mode}")
    print(f"{'='*60}\n")

    # Ensure tables exist (creates daily_levels if missing)
    create_tables()

    db = SessionLocal()
    try:
        symbols = get_universe_symbols(args.universe, db)
        print(f"  Symbols in universe: {len(symbols)}")

        if not symbols:
            print("  No symbols found. Load symbols first with:")
            print("    python scripts/load_nifty500.py")
            sys.exit(0)

        # Build levels from the chosen mode
        if args.mode == "preopen":
            levels = load_from_preopen(db, symbols, trade_date)
        else:
            levels = load_seed_data(db, symbols, trade_date)

        print(f"  Levels computed   : {len(levels)}")
        skipped = len(symbols) - len(levels)
        if skipped > 0:
            print(f"  Symbols skipped   : {skipped} (no preopen snapshot found)")

        # Upsert into database
        written = upsert_levels(db, levels)
        print(f"  Rows written      : {written}")

    finally:
        db.close()

    print()
    if levels:
        print("Sample levels written:")
        for lv in levels[:5]:
            print(
                f"  {lv['symbol']:<14}  "
                f"H={lv['prev_high']:>8.2f}  "
                f"L={lv['prev_low']:>8.2f}  "
                f"C={lv['prev_close']:>8.2f}  "
                f"src={lv['source']}"
            )
        if len(levels) > 5:
            print(f"  ... and {len(levels) - 5} more.")

    print()
    print("Next step: run scoring to use these levels.")
    print(f"  python scripts/run_scoring.py --date {trade_date}")
    print()
    print("Query levels in SQLite:")
    print("  sqlite3 data/nifty_premarket.db")
    print(f"  SELECT symbol, prev_high, prev_low, prev_close, source")
    print(f"  FROM daily_levels WHERE trade_date = '{trade_date}' LIMIT 10;")
    print()


if __name__ == "__main__":
    main()
