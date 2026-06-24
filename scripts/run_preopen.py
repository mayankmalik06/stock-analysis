"""
scripts/run_preopen.py

CLI script to run the NSE pre-open market data collector.

Run (test mode — works any time of day):
    python scripts/run_preopen.py --test

Run (live mode — only works 9:00–9:15 am IST):
    python scripts/run_preopen.py

Run with a different universe:
    python scripts/run_preopen.py --test --universe nifty_50

In test mode:
  - Runs a single poll immediately (no time gate).
  - If the live NSE pre-open API is not reachable, uses the built-in
    mock dataset so you always get sample rows in the DB.

Prerequisites:
    Run scripts/load_nifty500.py first so there are symbols to match.
    (In test mode with mock data, symbols don't need to be loaded.)
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
from app.collectors.preopen import fetch_preopen_snapshot
from app.models import PreopenSnapshot


def main():
    parser = argparse.ArgumentParser(description="NSE Pre-Open Data Collector")
    parser.add_argument(
        "--test",
        action="store_true",
        default=False,
        help="Run in test mode (bypasses time gate, uses mock data if NSE unreachable)",
    )
    parser.add_argument(
        "--universe",
        choices=["nifty_500", "nifty_50", "custom_watchlist", "all"],
        default="nifty_500",
        help="Universe filter (default: nifty_500). "
             "Pass 'all' in test mode to save mock rows even before Nifty 500 is loaded.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print(f"NSE Pre-Open Collector  [universe={args.universe}, test_mode={args.test}]")
    print("=" * 60)

    create_tables()
    result = fetch_preopen_snapshot(universe=args.universe, test_mode=args.test)

    print("\nResult:")
    for k, v in result.items():
        print(f"  {k:<22} {v}")

    # Show a sample from preopen_snapshots
    db = SessionLocal()
    try:
        sample = (
            db.query(PreopenSnapshot)
            .order_by(PreopenSnapshot.snapshot_id.desc())
            .limit(10)
            .all()
        )
        total = db.query(PreopenSnapshot).count()

        print(f"\nTotal rows in preopen_snapshots: {total}")
        if sample:
            print("\nMost recent 10 snapshots:")
            print(f"  {'Symbol':<14} {'Time':<20} {'Prev Close':>10} {'IEP':>10} {'Gap%':>7} {'Buy Qty':>10} {'Sell Qty':>10}")
            print(f"  {'-'*14} {'-'*20} {'-'*10} {'-'*10} {'-'*7} {'-'*10} {'-'*10}")
            for s in sample:
                ts = s.snapshot_time.strftime("%Y-%m-%d %H:%M:%S") if s.snapshot_time else "N/A"
                print(
                    f"  {s.symbol:<14} {ts:<20} "
                    f"{(s.prev_close or 0):>10.2f} "
                    f"{(s.indicative_price or 0):>10.2f} "
                    f"{(s.gap_pct or 0):>7.2f}% "
                    f"{(s.buy_qty or 0):>10,} "
                    f"{(s.sell_qty or 0):>10,}"
                )
        else:
            print("\nNo snapshots in DB yet.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
