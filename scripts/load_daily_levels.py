#!/usr/bin/env python3
"""
scripts/load_daily_levels.py

Milestone 3.5 / 5 — CLI script to load previous-day OHLC levels into daily_levels.

For a given trade_date (e.g. 2026-06-25), this script fetches or approximates
the previous trading session's high, low, and close for all active symbols in
the chosen universe, then writes them into the daily_levels table.

Usage examples:
    # Seed deterministic test data (no network call — safe for local testing)
    python scripts/load_daily_levels.py --date 2026-06-25 --mode seed

    # Derive levels from existing preopen_snapshots (offline approximation)
    python scripts/load_daily_levels.py --date 2026-06-25 --mode preopen

    # [MILESTONE 5 — LIVE] Download real NSE EOD bhavcopy for the given date
    python scripts/load_daily_levels.py --date 2026-06-25 --mode bhavcopy

    # Live mode respects DATA_MODE env var (set DATA_MODE=live to default to bhavcopy)
    export DATA_MODE=live
    python scripts/load_daily_levels.py --date 2026-06-25

    # Load levels for Nifty 50 universe only
    python scripts/load_daily_levels.py --date 2026-06-25 --universe nifty_50

    # Load levels for custom watchlist only
    python scripts/load_daily_levels.py --date 2026-06-25 --universe custom_watchlist

Run from the project root:
    python scripts/load_daily_levels.py --date 2026-06-25 --mode bhavcopy

------------------------------------------------------------
Data source note (Milestone 5)
------------------------------------------------------------
Mode "bhavcopy" (NEW — Milestone 5):
  Downloads the official NSE EOD equities bhavcopy CSV for the given date.
  URL: https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
  Parses SYMBOL, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE (EQ series only).
  Falls back to a zipped alternate URL if the plain CSV is not available.
  Source tag: "NSE_BHAVCOPY"

Mode "preopen" (original):
  Derives prev_high/low from existing preopen_snapshots using a fixed ±1.5% band.
  Source tag: "PREOPEN_DERIVED"

Mode "seed":
  Inserts deterministic test rows — no network call required.
  Source tag: "SEED"

DATA_MODE env var:
  DATA_MODE=live    → --mode defaults to bhavcopy
  DATA_MODE=simulated (default) → --mode defaults to preopen
  An explicit --mode flag always overrides DATA_MODE.

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
from app.collectors.nse_bhavcopy import fetch_bhavcopy, BhavcopyCopyError

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
    # Determine default mode from DATA_MODE env var
    data_mode_env = os.environ.get("DATA_MODE", "simulated").lower()
    default_mode = "bhavcopy" if data_mode_env == "live" else "preopen"

    parser.add_argument(
        "--mode",
        type=str,
        default=default_mode,
        choices=["preopen", "seed", "bhavcopy"],
        help=(
            "Data source mode.\n"
            "  preopen:  derive from existing preopen_snapshots (default when DATA_MODE=simulated).\n"
            "  seed:     insert deterministic test data — no network call.\n"
            "  bhavcopy: download real NSE EOD bhavcopy CSV (default when DATA_MODE=live)."
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
# Mode: real NSE EOD bhavcopy (Milestone 5)
# ---------------------------------------------------------------------------

def load_from_bhavcopy(
    db, symbols: list[Symbol], trade_date: datetime.date
) -> list[dict]:
    """
    Download the official NSE EOD bhavcopy CSV for trade_date and extract
    prev_high, prev_low, prev_close for each symbol in the universe.

    The bhavcopy date == trade_date (the session we just finished).
    For a pre-market run on DAY+1, pass trade_date = DAY.

    Missing symbols (not in bhavcopy) are logged as warnings and skipped.
    Source tag: "NSE_BHAVCOPY"
    """
    universe_tickers = {sym.symbol for sym in symbols}

    try:
        bhavcopy_rows = fetch_bhavcopy(
            date=trade_date,
            universe_symbols=universe_tickers,
        )
    except BhavcopyCopyError as exc:
        logger.error("Bhavcopy download failed: %s", exc)
        logger.error(
            "Is %s a trading day? Check https://www.nseindia.com/all-reports",
            trade_date,
        )
        return []

    # Build a quick lookup: symbol -> bhavcopy row
    bhavcopy_map = {row["symbol"]: row for row in bhavcopy_rows}

    levels = []
    missing = []
    for sym in symbols:
        ticker = sym.symbol
        if ticker in bhavcopy_map:
            row = bhavcopy_map[ticker]
            levels.append({
                "trade_date": trade_date,
                "symbol":     ticker,
                "prev_high":  row["prev_high"],
                "prev_low":   row["prev_low"],
                "prev_close": row["prev_close"],
                "source":     "NSE_BHAVCOPY",
            })
        else:
            missing.append(ticker)

    if missing:
        logger.warning(
            "%d symbol(s) not found in bhavcopy: %s",
            len(missing),
            ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else ""),
        )

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

    data_mode_env = os.environ.get("DATA_MODE", "simulated")

    print(f"\n{'='*60}")
    print(f"  Milestone 3.5/5 — Load Daily Levels")
    print(f"{'='*60}")
    print(f"  Trade date : {trade_date}")
    print(f"  Universe   : {args.universe}")
    print(f"  Mode       : {args.mode}")
    print(f"  DATA_MODE  : {data_mode_env}")
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
        if args.mode == "bhavcopy":
            levels = load_from_bhavcopy(db, symbols, trade_date)
        elif args.mode == "preopen":
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
