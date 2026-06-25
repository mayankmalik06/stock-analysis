#!/usr/bin/env python3
"""
scripts/load_announcements.py

Milestone 6: Fetch live NSE announcements and store them as pending
events in the symbol_events table.

After running this script, run classify_events.py to classify the rows.

Run from project root:
    python scripts/load_announcements.py --date 2026-06-26
    python scripts/load_announcements.py --date 2026-06-26 --universe nifty_50
    python scripts/load_announcements.py --date 2026-06-26 --universe custom_watchlist

The script:
  1. Migrates the database schema (adds Milestone 6 columns if missing).
  2. Fetches announcements from NSE RSS + JSON API for the given date.
  3. Filters to the requested universe.
  4. Stores raw rows in symbol_events with event_type = NULL (pending).
  5. Prints a summary and sample of what was stored.

Milestone: 6 (Live NSE Announcements + Better Events)
"""

import sys
import os
import argparse
import datetime
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DIVIDER = "─" * 60


def run_migration():
    """Run Milestone 6 schema migration (adds source/headline/announced_at)."""
    import sqlite3
    from app.config import settings

    db_url = settings.database_url
    if db_url.startswith("sqlite:///"):
        db_path = db_url[len("sqlite:///"):]
    elif db_url.startswith("sqlite://"):
        db_path = db_url[len("sqlite://"):]
    else:
        logger.warning("Non-SQLite DB; skipping migration step.")
        return

    if not os.path.exists(db_path):
        logger.info("DB not found — will be created by create_tables().")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(symbol_events)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    migrations = [
        ("source",       "TEXT DEFAULT 'SEED'"),
        ("headline",     "TEXT"),
        ("announced_at", "TEXT"),
    ]

    changed = False
    for col, defn in migrations:
        if col not in existing_cols:
            cursor.execute(f"ALTER TABLE symbol_events ADD COLUMN {col} {defn}")
            logger.info("DB migration: added column symbol_events.%s", col)
            changed = True

    if changed:
        conn.commit()
    conn.close()


def print_sample(db, trade_date: datetime.date, n: int = 5):
    """Print the n most recently inserted NSE_ANNOUNCEMENTS rows."""
    from app.models import SymbolEvent

    rows = (
        db.query(SymbolEvent)
        .filter(SymbolEvent.trade_date == trade_date)
        .filter(SymbolEvent.source == "NSE_ANNOUNCEMENTS")
        .order_by(SymbolEvent.id.desc())
        .limit(n)
        .all()
    )

    total = (
        db.query(SymbolEvent)
        .filter(SymbolEvent.trade_date == trade_date)
        .filter(SymbolEvent.source == "NSE_ANNOUNCEMENTS")
        .count()
    )

    print(f"\n  NSE_ANNOUNCEMENTS rows in DB for {trade_date}: {total}")
    if rows:
        print(f"\n  Most recent {n} rows (pending classification):")
        print(f"  {'ID':<6} {'Symbol':<14} {'Announced':<20} {'Headline'}")
        print(f"  {'--':<6} {'------':<14} {'---------':<20} {'--------'}")
        for r in rows:
            ann = r.announced_at.strftime("%Y-%m-%d %H:%M") if r.announced_at else "N/A"
            headline = (r.headline or r.raw_text[:60] or "")[:55]
            print(f"  {r.id:<6} {r.symbol:<14} {ann:<20} {headline}")
    else:
        print("  No NSE_ANNOUNCEMENTS rows stored yet.")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Fetch NSE corporate announcements for a date and store as "
            "pending events in symbol_events."
        )
    )
    parser.add_argument(
        "--date",
        default=str(datetime.date.today()),
        help="Trade date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--universe",
        choices=["nifty_500", "nifty_50", "custom_watchlist", "all"],
        default="nifty_500",
        help="Universe filter (default: nifty_500)",
    )
    args = parser.parse_args()

    try:
        trade_date = datetime.date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: Invalid date format '{args.date}'. Use YYYY-MM-DD.")
        sys.exit(1)

    print(DIVIDER)
    print(f"  NSE Announcements Loader")
    print(f"  Trade date : {trade_date}")
    print(f"  Universe   : {args.universe}")
    print(DIVIDER)

    # 1. Ensure DB and tables exist
    from app.db import create_tables, SessionLocal
    create_tables()

    # 2. Apply Milestone 6 schema migration
    run_migration()

    # 3. Fetch and store
    from app.collectors.nse_announcements import fetch_announcements_for_date

    result = fetch_announcements_for_date(
        trade_date=trade_date,
        universe=args.universe,
    )

    # 4. Print summary
    print("\n── Fetch Summary ──────────────────────────────────────────")
    print(f"  RSS items fetched      : {result['rss_items']}")
    print(f"  JSON API items fetched : {result['api_items']}")
    print(f"  Unique items merged    : {result['unique_items']}")
    print(f"  Inserted (pending)     : {result['inserted']}")
    print(f"  Skipped (duplicate)    : {result['skipped_duplicate']}")
    print(f"  Skipped (not in univ.) : {result['skipped_no_symbol_match']}")
    print(f"  Stored as UNMAPPED     : {result['unmapped_stored']}")
    print(f"  Errors                 : {result['errors']}")
    print("──────────────────────────────────────────────────────────")

    # 5. Show sample
    db = SessionLocal()
    try:
        print_sample(db, trade_date)
    finally:
        db.close()

    print()

    if result["inserted"] > 0:
        print("✓ Announcements loaded. Now classify them:")
        print(f"  python scripts/classify_events.py --date {trade_date}")
    elif result["unique_items"] == 0:
        print(
            "⚠  No items returned from NSE feeds. "
            "This may be normal outside trading hours or on market holidays. "
            "Check your internet connection if unexpected."
        )
    else:
        print(
            "⚠  Items were fetched but none were new "
            "(all already in DB or outside the universe)."
        )


if __name__ == "__main__":
    main()
