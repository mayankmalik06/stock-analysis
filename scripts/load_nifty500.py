"""
scripts/load_nifty500.py

CLI script to load the Nifty 500 universe into the symbols table.

Run:
    python scripts/load_nifty500.py

What it does:
  1. Downloads the official Nifty 500 constituent CSV from NSE.
  2. Upserts rows into the symbols table.
  3. Sets in_nifty_500 = True for all current members.
  4. Sets in_nifty_500 = False for any symbols previously marked but
     no longer in the constituent list.
  5. Prints a summary and shows a sample of rows.

You must run this (or the API endpoint) before using the RSS,
announcements, or pre-open collectors.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)

from app.db import create_tables, SessionLocal
from app.collectors.universe import load_nifty500
from app.models import Symbol


def main():
    print("=" * 60)
    print("Nifty 500 Universe Loader")
    print("=" * 60)

    # Ensure tables exist (safe if already created)
    create_tables()

    result = load_nifty500()

    print("\nLoad result:")
    for k, v in result.items():
        print(f"  {k:<20} {v}")

    # Show a sample of rows from the symbols table
    db = SessionLocal()
    try:
        sample = (
            db.query(Symbol)
            .filter(Symbol.in_nifty_500 == True)
            .limit(10)
            .all()
        )
        total = db.query(Symbol).filter(Symbol.in_nifty_500 == True).count()

        print(f"\nTotal in_nifty_500 = True: {total}")
        print("\nSample rows (first 10):")
        print(f"  {'Symbol':<15} {'Company':<45} {'Sector':<30}")
        print(f"  {'-'*15} {'-'*45} {'-'*30}")
        for s in sample:
            print(f"  {s.symbol:<15} {(s.company_name or '')[:44]:<45} {(s.sector or '')[:29]:<30}")
    finally:
        db.close()

    print("\nDone. You can now run the RSS, announcements, and pre-open collectors.")


if __name__ == "__main__":
    main()
