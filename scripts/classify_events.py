#!/usr/bin/env python3
"""
scripts/classify_events.py

Reads seed events from data/seed_events.json, classifies each event using
the LLM classifier, and writes structured rows into the symbol_events table.

Safe to run multiple times — duplicate rows are skipped via the unique
constraint on (trade_date, symbol, raw_text_hash).

Run from project root:
    python scripts/classify_events.py
    python scripts/classify_events.py --dry-run   # mock classifier, no DB write
    python scripts/classify_events.py --date 2026-06-25

Milestone: 4 (AI Layer)
"""

import sys
import os
import json
import hashlib
import logging
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal, create_tables
from app.models import SymbolEvent
from app.ai.event_classifier import classify_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SEED_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "seed_events.json",
)


def _hash_text(text: str) -> str:
    """SHA-256 hash of text, truncated to 64 chars for the DB column."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:64]


def run(trade_date_str: str, dry_run: bool = False):
    if not os.path.exists(SEED_FILE):
        logger.error("Seed file not found: %s", SEED_FILE)
        sys.exit(1)

    with open(SEED_FILE, "r", encoding="utf-8") as f:
        seed_data = json.load(f)

    # Allow --date override; otherwise use the date from the JSON file
    file_date = seed_data.get("trade_date", str(datetime.date.today()))
    if trade_date_str:
        trade_date_str = trade_date_str
    else:
        trade_date_str = file_date

    try:
        trade_date = datetime.date.fromisoformat(trade_date_str)
    except ValueError:
        logger.error("Invalid date format: %s — use YYYY-MM-DD", trade_date_str)
        sys.exit(1)

    events = seed_data.get("events", [])
    logger.info(
        "Loaded %d events from seed file for date %s (dry_run=%s)",
        len(events), trade_date, dry_run,
    )

    create_tables()
    db = SessionLocal()

    inserted = 0
    skipped = 0
    errors = 0

    for item in events:
        symbol = item.get("symbol", "").upper().strip()
        raw_text = item.get("raw_text", "").strip()

        if not symbol:
            logger.warning("Skipping event with no symbol: %s", item)
            errors += 1
            continue

        # Classify via LLM (or mock if dry_run or no API key)
        if dry_run:
            # Force mock path by temporarily making api_key look absent
            from app.ai.event_classifier import _mock_result
            result = _mock_result(raw_text)
            result.setdefault("_mock", True)
        else:
            result = classify_event(
                symbol=symbol,
                trade_date=str(trade_date),
                raw_text=raw_text,
            )

        event_type = result["event_type"]
        sentiment = result["sentiment"]
        confidence = result["confidence"]
        label = result["label"]
        is_mock = result.get("_mock", False)

        logger.info(
            "  %-12s | %-18s | %-10s | conf=%.2f%s | %s",
            symbol,
            event_type,
            sentiment,
            confidence,
            " [MOCK]" if is_mock else "",
            label[:60],
        )

        if dry_run:
            skipped += 1
            continue

        # Write to DB — skip duplicates gracefully
        text_hash = _hash_text(raw_text)

        existing = (
            db.query(SymbolEvent)
            .filter(SymbolEvent.trade_date == trade_date)
            .filter(SymbolEvent.symbol == symbol)
            .filter(SymbolEvent.raw_text_hash == text_hash)
            .first()
        )

        if existing:
            logger.debug("Duplicate row for %s on %s — skipping.", symbol, trade_date)
            skipped += 1
            continue

        row = SymbolEvent(
            trade_date=trade_date,
            symbol=symbol,
            raw_text=raw_text,
            event_type=event_type,
            sentiment=sentiment,
            confidence=confidence,
            label=label,
            raw_text_hash=text_hash,
        )
        db.add(row)
        inserted += 1

    if not dry_run:
        db.commit()
        logger.info("Committed %d new rows. Skipped %d duplicates. Errors: %d.", inserted, skipped, errors)
    else:
        logger.info("Dry-run complete. Would have inserted %d rows (no DB writes).", len(events))

    db.close()

    print("\n── Classification Summary ─────────────────────────────────")
    print(f"  Trade date : {trade_date}")
    print(f"  Events     : {len(events)}")
    if dry_run:
        print(f"  Mode       : DRY RUN (no DB writes)")
    else:
        print(f"  Inserted   : {inserted}")
        print(f"  Skipped    : {skipped} (duplicates)")
        print(f"  Errors     : {errors}")
    print("──────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Classify seed events and write to symbol_events table."
    )
    parser.add_argument(
        "--date",
        default="",
        help="Trade date in YYYY-MM-DD format (defaults to date in seed file)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run classifier and print results without writing to the database",
    )
    args = parser.parse_args()
    run(trade_date_str=args.date, dry_run=args.dry_run)
