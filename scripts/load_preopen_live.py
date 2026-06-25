#!/usr/bin/env python3
"""
scripts/load_preopen_live.py

Milestone 5 — Fetch live NSE pre-open data and write it into preopen_snapshots.

This script replaces the seed/mock-based pre-open flow when DATA_MODE=live.
It uses the existing NSE pre-open API endpoint (same as app/collectors/preopen.py)
but adds explicit --date support and structured logging so it fits naturally
into the daily CLI workflow.

Usage examples:
    # Fetch pre-open data for today (during 9:00–9:15 am IST)
    python scripts/load_preopen_live.py

    # Fetch for a specific date (useful for back-filling or dry-runs)
    python scripts/load_preopen_live.py --date 2026-06-26

    # Run outside market hours (forces a single poll regardless of time)
    python scripts/load_preopen_live.py --date 2026-06-26 --force

    # Use a narrower universe
    python scripts/load_preopen_live.py --universe nifty_50

    # Run a live multi-poll session (polls every 2 min until 9:15 am)
    python scripts/load_preopen_live.py --session

Run from the project root:
    python scripts/load_preopen_live.py --date 2026-06-26 --force

------------------------------------------------------------
Data source
------------------------------------------------------------
NSE pre-open API endpoint:
  https://www.nseindia.com/api/market-data-pre-open?key=ALL

This is the same endpoint used by app/collectors/preopen.py.
The script seeds NSE cookies by hitting the homepage first, exactly
as the existing collector does.

Fields written to preopen_snapshots:
  symbol            — NSE ticker
  snapshot_time     — time the snapshot was taken (IST, naive datetime)
  prev_close        — previous session close from NSE
  indicative_price  — indicative equilibrium price (IEP)
  gap_pct           — (IEP - prev_close) / prev_close * 100
  buy_qty           — total buy quantity at IEP
  sell_qty          — total sell quantity at IEP
  indicative_volume — total traded volume
  indicative_value  — IEP * indicative_volume

------------------------------------------------------------
Integration with scoring
------------------------------------------------------------
run_scoring.py and the scoring services read preopen_snapshots by
(symbol, snapshot_time) date range for the chosen trade_date.
Rows written by this script are consumed automatically — no change to
scoring logic is needed.

------------------------------------------------------------
DATA_MODE interaction
------------------------------------------------------------
This script is the "live pre-open" counterpart to run_preopen.py (which
uses the existing collector with mock fallback).

When DATA_MODE=live, the recommended morning workflow is:
    python scripts/load_preopen_live.py --date TODAY

When DATA_MODE=simulated, continue using:
    python scripts/run_preopen.py --test
"""

import argparse
import datetime
import logging
import sys
import os
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from app.db import SessionLocal, create_tables
from app.models import PreopenSnapshot
from app.collectors.universe import get_universe_symbols

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── NSE pre-open API ───────────────────────────────────────────────────────────

NSE_PREOPEN_URL = "https://www.nseindia.com/api/market-data-pre-open?key=ALL"
NSE_HOMEPAGE    = "https://www.nseindia.com"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market",
}

PREOPEN_START = datetime.time(9, 0)
PREOPEN_END   = datetime.time(9, 15)

MAX_RETRIES  = 3
RETRY_DELAY  = 5   # seconds


# ── Argument parsing ───────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch live NSE pre-open data and write to preopen_snapshots."
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
        help="Symbol universe to fetch pre-open data for. Default: nifty_500.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Force a single poll regardless of the current time. "
            "Use this for dry-runs or testing outside market hours."
        ),
    )
    parser.add_argument(
        "--session",
        action="store_true",
        help=(
            "Run continuous polling for the full pre-open session "
            "(9:00–9:15 am IST, polling every 2 minutes). "
            "Overrides --force."
        ),
    )
    return parser.parse_args()


# ── NSE HTTP helpers ───────────────────────────────────────────────────────────

def _make_nse_session() -> requests.Session:
    """Seed an NSE requests session with homepage cookies."""
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        resp = session.get(NSE_HOMEPAGE, timeout=15)
        resp.raise_for_status()
        logger.debug("NSE session seeded (status %s)", resp.status_code)
        time.sleep(1)
    except Exception as exc:
        logger.warning("Could not seed NSE session: %s — continuing anyway", exc)
    return session


def _fetch_preopen_raw(session: requests.Session) -> list[dict]:
    """
    Call the NSE pre-open API and return the raw data list.
    Retries up to MAX_RETRIES times on transient errors.
    Returns an empty list if the API is unreachable.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(NSE_PREOPEN_URL, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                records = data.get("data", [])
            elif isinstance(data, list):
                records = data
            else:
                records = []
            logger.info("Pre-open API returned %d records (attempt %d)", len(records), attempt)
            return records
        except Exception as exc:
            logger.warning("Pre-open API attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    logger.error("Pre-open API unreachable after %d attempts.", MAX_RETRIES)
    return []


# ── Record parser ──────────────────────────────────────────────────────────────

def _parse_record(rec: dict) -> dict | None:
    """
    Extract the fields we care about from one NSE pre-open record.
    NSE wraps data under a "metadata" key in some versions.
    Returns None if the record cannot be parsed.
    """
    try:
        meta = rec.get("metadata", rec)

        symbol     = str(meta.get("symbol", "")).strip().upper()
        if not symbol:
            return None

        iep        = float(meta.get("iep") or 0)
        prev_close = float(meta.get("previousClose") or meta.get("prevClose") or 0)
        buy_qty    = int(meta.get("totalBuyQuantity") or 0)
        sell_qty   = int(meta.get("totalSellQuantity") or 0)
        ind_vol    = int(meta.get("totalTradedVolume") or 0)

        gap_pct   = round((iep - prev_close) / prev_close * 100, 4) if prev_close > 0 else 0.0
        ind_value = round(iep * ind_vol, 2) if iep and ind_vol else 0.0

        return {
            "symbol":            symbol,
            "indicative_price":  iep,
            "prev_close":        prev_close,
            "gap_pct":           gap_pct,
            "buy_qty":           buy_qty,
            "sell_qty":          sell_qty,
            "indicative_volume": ind_vol,
            "indicative_value":  ind_value,
        }
    except Exception as exc:
        logger.debug("Could not parse pre-open record: %s — %s", rec, exc)
        return None


# ── Snapshot writer ────────────────────────────────────────────────────────────

def _write_snapshots(
    records:       list[dict],
    known_symbols: set[str],
    snapshot_time: datetime.datetime,
    db,
) -> dict:
    """
    Parse and write pre-open records to preopen_snapshots.
    Filters to known_symbols if provided (non-empty set).
    Returns {"saved": N, "skipped": M}.
    """
    saved   = 0
    skipped = 0

    for rec in records:
        parsed = _parse_record(rec)
        if parsed is None:
            skipped += 1
            continue

        symbol = parsed["symbol"]
        if known_symbols and symbol not in known_symbols:
            skipped += 1
            continue

        snap = PreopenSnapshot(
            symbol            = symbol,
            snapshot_time     = snapshot_time,
            prev_close        = parsed["prev_close"],
            indicative_price  = parsed["indicative_price"],
            gap_pct           = parsed["gap_pct"],
            buy_qty           = parsed["buy_qty"],
            sell_qty          = parsed["sell_qty"],
            indicative_volume = parsed["indicative_volume"],
            indicative_value  = parsed["indicative_value"],
        )
        db.add(snap)
        saved += 1

    db.commit()
    return {"saved": saved, "skipped": skipped}


# ── Single poll ────────────────────────────────────────────────────────────────

def run_single_poll(
    universe: str,
    snapshot_time: datetime.datetime,
    db,
) -> dict:
    """
    Perform one pre-open API poll and write results to the DB.
    Returns summary dict with saved/skipped counts.
    """
    known_symbols = set(get_universe_symbols(universe=universe))
    logger.info(
        "Single poll: universe='%s', symbols=%d, snapshot_time=%s",
        universe, len(known_symbols), snapshot_time
    )

    nse_session = _make_nse_session()
    records = _fetch_preopen_raw(nse_session)

    if not records:
        return {
            "universe":     universe,
            "source":       "live_nse",
            "records":      0,
            "saved":        0,
            "skipped":      0,
            "error":        "NSE returned no data",
        }

    counts = _write_snapshots(records, known_symbols, snapshot_time, db)
    return {
        "universe":     universe,
        "source":       "live_nse",
        "records":      len(records),
        "saved":        counts["saved"],
        "skipped":      counts["skipped"],
        "snapshot_time": snapshot_time.isoformat(),
    }


# ── Session polling loop ───────────────────────────────────────────────────────

def run_preopen_session_live(universe: str, poll_interval: int = 120, db=None) -> dict:
    """
    Poll the NSE pre-open API repeatedly from 9:00 to 9:15 am IST.
    poll_interval: seconds between polls (default 120 = 2 min).
    """
    total_polls = 0
    total_saved = 0

    logger.info(
        "Starting pre-open session: universe='%s', interval=%ds",
        universe, poll_interval
    )

    while True:
        now_ist = datetime.datetime.now(tz=IST)
        current_time = now_ist.time()

        if current_time > PREOPEN_END:
            logger.info("Pre-open session ended at %s IST. Stopping.", PREOPEN_END)
            break

        if current_time < PREOPEN_START:
            wait_secs = (
                datetime.datetime.combine(now_ist.date(), PREOPEN_START, tzinfo=IST)
                - now_ist
            ).total_seconds()
            logger.info("Waiting %.0f s until pre-open starts.", wait_secs)
            time.sleep(min(wait_secs, 30))
            continue

        snapshot_time = now_ist.replace(tzinfo=None)
        result = run_single_poll(universe=universe, snapshot_time=snapshot_time, db=db)
        total_polls += 1
        total_saved += result.get("saved", 0)

        logger.info(
            "Poll %d: saved=%d, skipped=%d",
            total_polls, result.get("saved", 0), result.get("skipped", 0)
        )

        time.sleep(poll_interval)

    return {"total_polls": total_polls, "total_saved": total_saved}


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

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
    print(f"  Milestone 5 — Load Live Pre-Open Snapshots")
    print(f"{'='*60}")
    print(f"  Trade date : {trade_date}")
    print(f"  Universe   : {args.universe}")
    print(f"  DATA_MODE  : {data_mode_env}")
    print(f"  Mode       : {'session' if args.session else 'force-single' if args.force else 'time-gated'}")
    print(f"{'='*60}\n")

    create_tables()
    db = SessionLocal()

    try:
        if args.session:
            # Multi-poll session mode (waits for 9:00 am if needed)
            result = run_preopen_session_live(universe=args.universe, db=db)
            print(f"\nSession complete: {result['total_polls']} polls, {result['total_saved']} rows saved.")

        elif args.force:
            # Single forced poll — ignores time of day
            now_ist = datetime.datetime.now(tz=IST).replace(tzinfo=None)
            result = run_single_poll(
                universe=args.universe,
                snapshot_time=now_ist,
                db=db,
            )
            _print_single_result(result, trade_date)

        else:
            # Time-gated single poll — only runs during 9:00–9:15 am IST
            now_ist = datetime.datetime.now(tz=IST)
            current_time = now_ist.time()
            if not (PREOPEN_START <= current_time <= PREOPEN_END):
                print(
                    f"  Pre-open window is {PREOPEN_START}–{PREOPEN_END} IST.\n"
                    f"  Current IST time: {current_time.strftime('%H:%M:%S')}\n"
                    f"  Use --force to run outside market hours (dry-run / testing).\n"
                    f"  Use --session to wait for and poll the full session."
                )
                sys.exit(0)

            snapshot_time = now_ist.replace(tzinfo=None)
            result = run_single_poll(
                universe=args.universe,
                snapshot_time=snapshot_time,
                db=db,
            )
            _print_single_result(result, trade_date)

    finally:
        db.close()


def _print_single_result(result: dict, trade_date: datetime.date):
    """Print a concise summary after a single poll."""
    if result.get("error"):
        print(f"  ERROR: {result['error']}")
        print("  The NSE pre-open endpoint may be unavailable outside market hours.")
        print("  Use mock/seed data for testing: python scripts/run_preopen.py --test")
        return

    print(f"  Records from NSE : {result['records']}")
    print(f"  Rows saved       : {result['saved']}")
    print(f"  Rows skipped     : {result['skipped']} (not in universe)")
    print(f"  Snapshot time    : {result.get('snapshot_time', 'N/A')}")
    print()
    print("Next step: run scoring to consume these snapshots.")
    print(f"  python scripts/run_scoring.py --date {trade_date}")
    print()
    print("Query snapshots in SQLite:")
    print("  sqlite3 data/nifty_premarket.db")
    print(
        f"  SELECT symbol, indicative_price, gap_pct, prev_close"
        f" FROM preopen_snapshots"
        f" ORDER BY snapshot_time DESC LIMIT 10;"
    )
    print()


if __name__ == "__main__":
    main()
