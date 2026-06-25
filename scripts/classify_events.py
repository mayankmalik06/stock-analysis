#!/usr/bin/env python3
"""
scripts/classify_events.py

Milestone 6 update: Reads UNCLASSIFIED rows from symbol_events for a given
trade_date, runs the LLM event classifier, and updates each row with:
    event_type, sentiment, confidence, label

Backward compatible: if no live rows are found and a seed JSON file exists,
it falls back to loading from data/seed_events.json (Milestone 4 behaviour).

Safe to run multiple times — already-classified rows (event_type IS NOT NULL)
are skipped unless --reclassify is passed.

Run from project root:
    python scripts/classify_events.py --date 2026-06-26
    python scripts/classify_events.py --date 2026-06-26 --dry-run
    python scripts/classify_events.py --date 2026-06-26 --reclassify
    python scripts/classify_events.py --date 2026-06-26 --source NSE_ANNOUNCEMENTS

Milestone: 6 (Live NSE Announcements + Better Events)
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

DIVIDER = "─" * 60


def _hash_text(text: str) -> str:
    """SHA-256 hash of text, truncated to 64 chars for the DB column."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:64]


# ── Path A: classify live symbol_events rows (Milestone 6) ────────────────────

def classify_live_rows(
    trade_date: datetime.date,
    dry_run: bool,
    reclassify: bool,
    source_filter: str | None,
) -> dict:
    """
    Read unclassified (or all, if reclassify) rows from symbol_events for
    trade_date and classify each one via the LLM.

    Returns a summary dict.
    """
    db = SessionLocal()
    try:
        query = db.query(SymbolEvent).filter(
            SymbolEvent.trade_date == trade_date
        )

        # Optionally filter to a specific source (e.g. NSE_ANNOUNCEMENTS)
        if source_filter:
            query = query.filter(SymbolEvent.source == source_filter)

        # Skip already-classified rows unless --reclassify
        if not reclassify:
            query = query.filter(SymbolEvent.event_type == None)  # noqa: E711

        rows = query.order_by(SymbolEvent.id.asc()).all()

        logger.info(
            "Found %d rows to classify for %s (reclassify=%s, source=%s)",
            len(rows), trade_date, reclassify, source_filter or "all",
        )

        if not rows:
            return {
                "mode": "live",
                "trade_date": str(trade_date),
                "rows_found": 0,
                "classified": 0,
                "skipped": 0,
                "errors": 0,
            }

        classified = 0
        skipped = 0
        errors = 0

        for row in rows:
            symbol = row.symbol
            raw_text = row.raw_text or ""

            if not raw_text.strip():
                logger.debug("Empty raw_text for row id=%d (%s) — skipping.", row.id, symbol)
                if not dry_run:
                    row.event_type = "NO_EVENT"
                    row.sentiment = "NEUTRAL"
                    row.confidence = 1.0
                    row.label = "No event text provided"
                skipped += 1
                continue

            try:
                if dry_run:
                    from app.ai.event_classifier import _mock_result
                    result = _mock_result(raw_text)
                    result["_mock"] = True
                else:
                    result = classify_event(
                        symbol=symbol,
                        trade_date=str(trade_date),
                        raw_text=raw_text,
                    )

                is_mock = result.get("_mock", False)
                logger.info(
                    "  id=%-6d %-14s | %-18s | %-10s | conf=%.2f%s | %s",
                    row.id,
                    symbol,
                    result["event_type"],
                    result["sentiment"],
                    result["confidence"],
                    " [MOCK]" if is_mock else "",
                    result["label"][:55],
                )

                if not dry_run:
                    row.event_type = result["event_type"]
                    row.sentiment = result["sentiment"]
                    row.confidence = result["confidence"]
                    row.label = result["label"]
                    classified += 1

            except Exception as exc:
                logger.error("Error classifying row id=%d (%s): %s", row.id, symbol, exc)
                errors += 1
                continue

        if not dry_run:
            db.commit()
            logger.info(
                "Committed: classified=%d, skipped=%d, errors=%d",
                classified, skipped, errors,
            )

        return {
            "mode": "live",
            "trade_date": str(trade_date),
            "rows_found": len(rows),
            "classified": classified if not dry_run else 0,
            "dry_run_previewed": len(rows) if dry_run else 0,
            "skipped": skipped,
            "errors": errors,
        }

    finally:
        db.close()


# ── Path B: seed JSON fallback (Milestone 4 backward compat) ──────────────────

def classify_seed_rows(
    trade_date: datetime.date,
    dry_run: bool,
) -> dict:
    """
    Fallback: load seed events from data/seed_events.json and insert as
    new symbol_events rows (classified in the same pass).

    Used when no live rows exist for the date and the seed file is present.
    """
    if not os.path.exists(SEED_FILE):
        logger.warning("Seed file not found at %s.", SEED_FILE)
        return {
            "mode": "seed",
            "trade_date": str(trade_date),
            "rows_found": 0,
            "classified": 0,
            "skipped": 0,
            "errors": 0,
        }

    with open(SEED_FILE, "r", encoding="utf-8") as f:
        seed_data = json.load(f)

    events = seed_data.get("events", [])
    logger.info("Seed file: %d events loaded (date override: %s).", len(events), trade_date)

    create_tables()
    db = SessionLocal()

    inserted = 0
    skipped = 0
    errors = 0

    for item in events:
        symbol = item.get("symbol", "").upper().strip()
        raw_text = item.get("raw_text", "").strip()

        if not symbol:
            logger.warning("Skipping seed event with no symbol: %s", item)
            errors += 1
            continue

        try:
            if dry_run:
                from app.ai.event_classifier import _mock_result
                result = _mock_result(raw_text)
                result["_mock"] = True
            else:
                result = classify_event(
                    symbol=symbol,
                    trade_date=str(trade_date),
                    raw_text=raw_text,
                )

            is_mock = result.get("_mock", False)
            logger.info(
                "  %-12s | %-18s | %-10s | conf=%.2f%s | %s",
                symbol,
                result["event_type"],
                result["sentiment"],
                result["confidence"],
                " [MOCK]" if is_mock else "",
                result["label"][:55],
            )

            if dry_run:
                skipped += 1
                continue

            text_hash = _hash_text(raw_text)
            existing = (
                db.query(SymbolEvent)
                .filter(SymbolEvent.trade_date == trade_date)
                .filter(SymbolEvent.symbol == symbol)
                .filter(SymbolEvent.raw_text_hash == text_hash)
                .first()
            )

            if existing:
                logger.debug("Duplicate seed row for %s on %s — skipping.", symbol, trade_date)
                skipped += 1
                continue

            row = SymbolEvent(
                trade_date=trade_date,
                symbol=symbol,
                raw_text=raw_text,
                source="SEED",
                headline=raw_text[:200],
                announced_at=None,
                event_type=result["event_type"],
                sentiment=result["sentiment"],
                confidence=result["confidence"],
                label=result["label"],
                raw_text_hash=text_hash,
            )
            db.add(row)
            inserted += 1

        except Exception as exc:
            logger.error("Error classifying seed event %s: %s", item, exc)
            errors += 1
            continue

    if not dry_run:
        db.commit()
        logger.info("Seed classification committed: %d rows.", inserted)

    db.close()

    return {
        "mode": "seed",
        "trade_date": str(trade_date),
        "rows_found": len(events),
        "classified": inserted if not dry_run else 0,
        "dry_run_previewed": len(events) if dry_run else 0,
        "skipped": skipped,
        "errors": errors,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    trade_date_str: str,
    dry_run: bool = False,
    reclassify: bool = False,
    source_filter: str | None = None,
):
    try:
        trade_date = datetime.date.fromisoformat(trade_date_str)
    except ValueError:
        logger.error("Invalid date format: %s — use YYYY-MM-DD", trade_date_str)
        sys.exit(1)

    create_tables()

    # ── Check if live rows exist for this date ─────────────────────────────
    db = SessionLocal()
    try:
        live_count = (
            db.query(SymbolEvent)
            .filter(SymbolEvent.trade_date == trade_date)
            .filter(SymbolEvent.source == "NSE_ANNOUNCEMENTS")
            .count()
        )
        total_count = (
            db.query(SymbolEvent)
            .filter(SymbolEvent.trade_date == trade_date)
            .count()
        )
    finally:
        db.close()

    logger.info(
        "symbol_events for %s: total=%d, NSE_ANNOUNCEMENTS=%d",
        trade_date, total_count, live_count,
    )

    if live_count > 0 or total_count > 0:
        # Prefer classifying live DB rows
        result = classify_live_rows(
            trade_date=trade_date,
            dry_run=dry_run,
            reclassify=reclassify,
            source_filter=source_filter,
        )
    else:
        # No live rows — fall back to seed file (Milestone 4 compat)
        logger.info(
            "No symbol_events rows found for %s — falling back to seed file.",
            trade_date,
        )
        result = classify_seed_rows(trade_date=trade_date, dry_run=dry_run)

    # ── Print summary ──────────────────────────────────────────────────────
    print(f"\n{DIVIDER}")
    print("  Classification Summary")
    print(DIVIDER)
    print(f"  Trade date       : {result['trade_date']}")
    print(f"  Mode             : {result['mode']}")
    if dry_run:
        print(f"  Mode             : DRY RUN (no DB writes)")
        print(f"  Previewed        : {result.get('dry_run_previewed', 0)} rows")
    else:
        print(f"  Rows found       : {result['rows_found']}")
        print(f"  Classified       : {result['classified']}")
        print(f"  Skipped          : {result['skipped']}")
        print(f"  Errors           : {result['errors']}")
    print(DIVIDER + "\n")

    # Show sample query hint
    if not dry_run and result.get("classified", 0) > 0:
        print(
            "  Sample DB query to verify:\n"
            f"  SELECT symbol, event_type, sentiment, confidence, label\n"
            f"  FROM symbol_events\n"
            f"  WHERE trade_date = '{trade_date}'\n"
            f"  ORDER BY id DESC LIMIT 10;\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Classify pending symbol_events rows for a given date. "
            "Falls back to seed JSON if no live rows are present."
        )
    )
    parser.add_argument(
        "--date",
        default=str(datetime.date.today()),
        help="Trade date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run classifier and print results without writing to the database",
    )
    parser.add_argument(
        "--reclassify",
        action="store_true",
        help=(
            "Re-classify rows that already have an event_type. "
            "Default: only classify NULL (unclassified) rows."
        ),
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Only classify rows from this source (e.g. NSE_ANNOUNCEMENTS)",
    )
    args = parser.parse_args()
    run(
        trade_date_str=args.date,
        dry_run=args.dry_run,
        reclassify=args.reclassify,
        source_filter=args.source,
    )
