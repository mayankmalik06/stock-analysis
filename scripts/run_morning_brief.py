#!/usr/bin/env python3
"""
scripts/run_morning_brief.py

CLI entry point for generating and printing the morning brief.

Assumptions:
    - Snapshots, levels, scoring, and events have already been computed
      for the given trade_date before this script is called.
    - The database contains daily_rankings and symbol_events for the date.

Run from project root:
    python scripts/run_morning_brief.py
    python scripts/run_morning_brief.py --date 2026-06-25
    python scripts/run_morning_brief.py --date 2026-06-25 --save

What it prints:
    1. A short summary (top 5 symbols + key events) to stdout.
    2. The full rendered brief text.

With --save, also writes the brief to data/brief_{date}.md

Milestone: 4 (AI Layer)
"""

import sys
import os
import argparse
import datetime
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal, create_tables
from app.ai.morning_brief import generate_brief

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DIVIDER = "─" * 60


def print_summary(result: dict):
    """Print the short top-5 summary to stdout."""
    trade_date = result["trade_date"]
    top_symbols = result["top_symbols"]
    sections = result["sections"]

    print(f"\n{DIVIDER}")
    print(f"  NIFTY PRE-MARKET BRIEF — {trade_date}")
    print(DIVIDER)

    print("\n  TOP WATCHLIST (today's ranked board)")
    print(f"  {'Rank':<5} {'Symbol':<12} {'Bucket':<7} {'Score':<7} {'Catalyst':<6} {'Preopen':<8} Event")
    print(f"  {'----':<5} {'------':<12} {'------':<7} {'-----':<7} {'-------':<6} {'-------':<8} -----")

    for s in top_symbols:
        tags = " | ".join(
            f"{t['event_type']}/{t['sentiment']}"
            for t in s.get("event_tags", [])[:2]
        ) or "—"
        print(
            f"  {s['rank']:<5} {s['symbol']:<12} {s['bucket']:<7} "
            f"{s['total_score']:<7.1f} {s['catalyst_score']:<6.0f} "
            f"{s['preopen_score']:<8.0f} {tags}"
        )

    print(f"\n  Positive catalysts : {', '.join(sections.get('positive_catalysts', [])) or 'None'}")
    print(f"  Risk names         : {', '.join(sections.get('risk_names', [])) or 'None'}")
    print(f"  Secondary watch    : {', '.join(sections.get('noisy_items', [])) or 'None'}")
    print(f"\n{DIVIDER}\n")


def print_full_brief(rendered_brief: str):
    """Print the full rendered brief."""
    print("\n── FULL MORNING BRIEF ──────────────────────────────────────\n")
    print(rendered_brief)
    print(f"\n{DIVIDER}\n")


def save_brief(result: dict):
    """Save the brief to a markdown file in data/."""
    trade_date = result["trade_date"]
    data_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
    )
    os.makedirs(data_dir, exist_ok=True)
    filepath = os.path.join(data_dir, f"brief_{trade_date}.md")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(result["rendered_brief"])

    print(f"  Brief saved to: {filepath}")


def run(trade_date: datetime.date, save: bool = False):
    create_tables()
    db = SessionLocal()

    try:
        result = generate_brief(db=db, trade_date=trade_date)
    except ValueError as e:
        print(f"\nERROR: {e}\n")
        db.close()
        sys.exit(1)
    finally:
        db.close()

    print_summary(result)
    print_full_brief(result["rendered_brief"])

    if save:
        save_brief(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate and print the morning pre-market brief."
    )
    parser.add_argument(
        "--date",
        default=str(datetime.date.today()),
        help="Trade date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save the rendered brief to data/brief_{date}.md",
    )
    args = parser.parse_args()

    try:
        trade_date = datetime.date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: Invalid date format '{args.date}'. Use YYYY-MM-DD.")
        sys.exit(1)

    run(trade_date=trade_date, save=args.save)
