"""
scripts/run_rss.py

CLI script to run the NSE RSS feed collector.

Run:
    python scripts/run_rss.py
    python scripts/run_rss.py --universe nifty_50
    python scripts/run_rss.py --universe custom_watchlist

Prerequisites:
    Run scripts/load_nifty500.py first so there are symbols to match against.
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)

from app.db import create_tables, SessionLocal
from app.collectors.nse_rss import fetch_nse_rss_feeds
from app.models import Event


def main():
    parser = argparse.ArgumentParser(description="NSE RSS Feed Collector")
    parser.add_argument(
        "--universe",
        choices=["nifty_500", "nifty_50", "custom_watchlist", "all"],
        default="nifty_500",
        help="Universe filter (default: nifty_500)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print(f"NSE RSS Collector  [universe={args.universe}]")
    print("=" * 60)

    create_tables()
    result = fetch_nse_rss_feeds(universe=args.universe)

    print("\nResult:")
    for k, v in result.items():
        print(f"  {k:<20} {v}")

    # Show a sample of the most recently inserted RSS events
    db = SessionLocal()
    try:
        sample = (
            db.query(Event)
            .filter(Event.source == "nse_rss")
            .order_by(Event.event_id.desc())
            .limit(5)
            .all()
        )
        total = db.query(Event).filter(Event.source == "nse_rss").count()

        print(f"\nTotal nse_rss events in DB: {total}")
        if sample:
            print("\nMost recent 5 RSS events:")
            for e in sample:
                print(f"  [{e.event_id}] {e.symbol:<12} | {e.headline[:70]}")
        else:
            print("\nNo RSS events in DB yet.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
