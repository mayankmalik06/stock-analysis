"""
scripts/run_announcements.py

CLI script to run the NSE corporate announcements collector.

Run:
    python scripts/run_announcements.py
    python scripts/run_announcements.py --hours 48
    python scripts/run_announcements.py --universe nifty_50

Prerequisites:
    Run scripts/load_nifty500.py first.
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
from app.collectors.nse_announcements import fetch_nse_announcements
from app.models import Event


def main():
    parser = argparse.ArgumentParser(description="NSE Announcements Collector")
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="How many hours back to fetch announcements (default: 24)",
    )
    parser.add_argument(
        "--universe",
        choices=["nifty_500", "nifty_50", "custom_watchlist", "all"],
        default="nifty_500",
        help="Universe filter (default: nifty_500)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print(f"NSE Announcements Collector  [universe={args.universe}, hours={args.hours}]")
    print("=" * 60)

    create_tables()
    result = fetch_nse_announcements(since_hours=args.hours, universe=args.universe)

    print("\nResult:")
    for k, v in result.items():
        print(f"  {k:<25} {v}")

    # Show a sample of the most recently inserted announcement events
    db = SessionLocal()
    try:
        sample = (
            db.query(Event)
            .filter(Event.source == "nse_announcement")
            .order_by(Event.event_id.desc())
            .limit(5)
            .all()
        )
        total = db.query(Event).filter(Event.source == "nse_announcement").count()

        print(f"\nTotal nse_announcement events in DB: {total}")
        if sample:
            print("\nMost recent 5 announcement events:")
            for e in sample:
                ts = e.event_timestamp.strftime("%Y-%m-%d %H:%M") if e.event_timestamp else "N/A"
                print(f"  [{e.event_id}] {e.symbol:<12} | {ts} | {e.headline[:60]}")
        else:
            print("\nNo announcement events in DB yet.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
