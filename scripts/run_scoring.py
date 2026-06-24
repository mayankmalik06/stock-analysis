#!/usr/bin/env python3
"""
scripts/run_scoring.py

CLI script to trigger the Milestone 3 scoring pipeline.

Usage examples:
    # Score today with default Nifty 500 universe
    python scripts/run_scoring.py

    # Score a specific date
    python scripts/run_scoring.py --date 2026-06-25

    # Score Nifty 50 only
    python scripts/run_scoring.py --universe nifty_50

    # Score with a longer event window (48 hours of events)
    python scripts/run_scoring.py --event-window 48

    # Show only A-grade symbols after scoring
    python scripts/run_scoring.py --show-bucket A

    # Run and show top 10 results
    python scripts/run_scoring.py --top 10

Run from the project root (where app/ lives):
    python scripts/run_scoring.py
"""

import argparse
import datetime
import json
import sys
import os

# Make sure app/ is importable when running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal, create_tables
from app.services.ranking import run_scoring, get_top_rankings


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the Nifty Pre-Market Briefing scoring pipeline."
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
        default="nifty_500",
        choices=["nifty_500", "nifty_50", "custom_watchlist", "all"],
        help="Symbol universe to score. Default: nifty_500.",
    )
    parser.add_argument(
        "--event-window",
        type=int,
        default=24,
        help="Hours of events to look back for catalyst scoring. Default: 24.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Show only the top N results. Default: show all.",
    )
    parser.add_argument(
        "--show-bucket",
        type=str,
        default=None,
        choices=["A", "B", "C"],
        help="After scoring, show only symbols in this bucket (A, B, or C).",
    )
    return parser.parse_args()


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
    print(f"  Nifty Pre-Market Scoring Run")
    print(f"{'='*60}")
    print(f"  Trade date     : {trade_date}")
    print(f"  Universe       : {args.universe}")
    print(f"  Event window   : last {args.event_window} hours")
    print(f"{'='*60}\n")

    # Ensure tables exist (safe to call every time)
    create_tables()

    db = SessionLocal()
    try:
        result = run_scoring(
            db=db,
            trade_date=trade_date,
            universe=args.universe,
            event_window_hours=args.event_window,
        )
    finally:
        db.close()

    # ── Print summary ────────────────────────────────────────────────
    print(f"Scoring complete.")
    print(f"  Symbols scored : {result['symbols_scored']}")
    if result.get("bucket_counts"):
        bc = result["bucket_counts"]
        print(f"  Bucket A (>=70): {bc.get('A', 0)}")
        print(f"  Bucket B (50–69): {bc.get('B', 0)}")
        print(f"  Bucket C (<50) : {bc.get('C', 0)}")
    print()

    if result["symbols_scored"] == 0:
        print("No symbols scored. Check that symbols and data have been loaded.")
        print("Hint: POST /collectors/load-nifty500 first, then run collectors.")
        sys.exit(0)

    # ── Print results table ──────────────────────────────────────────
    all_results = result.get("results", [])

    # Apply filters
    if args.show_bucket:
        filtered = [r for r in all_results if r["watchlist_bucket"] == args.show_bucket]
    else:
        filtered = all_results

    if args.top:
        filtered = filtered[:args.top]

    if not filtered:
        print(f"No results to display (bucket filter: {args.show_bucket}).")
        sys.exit(0)

    # ── Table header ──────────────────────────────────────────────────
    header = f"{'Rank':>4}  {'Symbol':<14} {'Bucket':>6}  {'Total':>6}  {'Cat':>6}  {'PO':>6}  {'Liq':>6}  {'Tech':>6}  {'Events':>6}  {'Snaps':>5}"
    print(header)
    print("-" * len(header))

    for r in filtered:
        print(
            f"{r['rank']:>4}  "
            f"{r['symbol']:<14} "
            f"{r['watchlist_bucket']:>6}  "
            f"{r['total_score']:>6.1f}  "
            f"{r['catalyst_score']:>6.1f}  "
            f"{r['preopen_score']:>6.1f}  "
            f"{r['liquidity_score']:>6.1f}  "
            f"{r['technical_score']:>6.1f}  "
            f"{r.get('event_count', 0):>6}  "
            f"{r.get('snapshot_count', 0):>5}"
        )

    print()
    print("Columns: Rank | Symbol | Bucket | TotalScore | Catalyst | PreOpen | Liquidity | Technical | Events | Snapshots")
    print()
    print("To query daily_rankings directly:")
    print("  sqlite3 data/nifty_premarket.db")
    print(f"  SELECT symbol, total_score, watchlist_bucket, rank FROM daily_rankings")
    print(f"  WHERE trade_date = '{trade_date}' ORDER BY rank LIMIT 20;")
    print()


if __name__ == "__main__":
    main()
