"""
app/collectors/nse_announcements.py

Collects corporate filings and announcements from the NSE corporate
announcements JSON API.

What it does:
  - Hits the NSE /api/corporate-announcements endpoint.
  - Filters results to symbols in the chosen universe.
  - Deduplicates against already-ingested events (by source_url).
  - Inserts new rows into the events table with source = "nse_announcement".

Universe filter (default: "nifty_500").

NSE API notes:
  - The endpoint requires a browser-like session cookie (nseappid) which is
    obtained by first visiting the NSE homepage.
  - We use a requests.Session to carry the cookie automatically.
  - If the API returns an empty/error response, we log a warning and return
    whatever was collected.

How to run:
    python scripts/run_announcements.py
    python scripts/run_announcements.py --hours 48
or via FastAPI:
    POST /collectors/run-announcements
    POST /collectors/run-announcements?since_hours=48&universe=nifty_500
"""

import logging
import datetime
import time
from zoneinfo import ZoneInfo

import requests
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Event
from app.collectors.universe import get_universe_symbols

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── NSE API constants ──────────────────────────────────────────────────────────
NSE_BASE_URL       = "https://www.nseindia.com"
NSE_ANNOUNCE_API   = (
    "https://www.nseindia.com/api/corporate-announcements"
    "?index=equities&from_date={from_date}&to_date={to_date}&offset=0&limit=100"
)
NSE_HOMEPAGE       = "https://www.nseindia.com"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}


# ── Session helper ─────────────────────────────────────────────────────────────

def _get_nse_session() -> requests.Session:
    """
    Create a requests.Session, visit the NSE homepage to obtain the required
    session cookies, then return the session for subsequent API calls.
    """
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get(NSE_HOMEPAGE, timeout=15)
        time.sleep(1)  # brief pause to appear more browser-like
    except Exception as exc:
        logger.warning("Could not pre-seed NSE session cookies: %s", exc)
    return session


# ── Timestamp parser ───────────────────────────────────────────────────────────

def _parse_nse_timestamp(ts_str: str | None) -> datetime.datetime | None:
    """
    NSE returns timestamps in several formats. Try the most common ones.
    Returns a naive datetime in IST.
    """
    if not ts_str:
        return None
    formats = [
        "%d-%b-%Y %H:%M:%S",   # 25-Jun-2026 09:30:00  ← NSE an_dt format
        "%d-%b-%Y",             # 25-Jun-2026
        "%Y-%m-%dT%H:%M:%S",   # ISO 8601
        "%Y-%m-%d %H:%M:%S",   # sort_date format
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(ts_str.strip(), fmt)
        except ValueError:
            continue
    logger.debug("Could not parse NSE timestamp: %s", ts_str)
    return datetime.datetime.now()


# ── Main collector ─────────────────────────────────────────────────────────────

def fetch_nse_announcements(
    since_hours: int = 24,
    universe: str = "nifty_500",
    db: Session | None = None,
) -> dict:
    """
    Fetch NSE corporate announcements published in the last N hours and
    store relevant ones in the events table.

    Parameters:
        since_hours — how many hours back to look (default 24)
        universe    — "nifty_500" (default), "nifty_50", "custom_watchlist", "all"
        db          — optional SQLAlchemy session

    Returns dict: universe, since_hours, api_records, inserted, skipped, errors
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        known_symbols = set(get_universe_symbols(universe=universe))
        logger.info(
            "Announcements collector: universe='%s', symbols=%d, last %d hours",
            universe, len(known_symbols), since_hours
        )

        now     = datetime.datetime.now()
        from_dt = now - datetime.timedelta(hours=since_hours)

        from_date = from_dt.strftime("%d-%m-%Y")
        to_date   = now.strftime("%d-%m-%Y")

        url = NSE_ANNOUNCE_API.format(from_date=from_date, to_date=to_date)
        logger.info("Fetching: %s", url)

        # Collect existing source_urls to avoid duplicates
        existing_urls: set[str] = {
            row[0]
            for row in db.query(Event.source_url)
            .filter(Event.source == "nse_announcement")
            .all()
            if row[0]
        }

        nse_session = _get_nse_session()

        try:
            response = nse_session.get(url, timeout=20)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.error("NSE announcements API error: %s", exc)
            data = []

        # NSE returns either a list directly or a dict with a "data" key
        if isinstance(data, dict):
            records = data.get("data", [])
        elif isinstance(data, list):
            records = data
        else:
            records = []

        logger.info("API returned %d records", len(records))

        inserted = 0
        skipped  = 0
        errors   = 0
        no_universe_match = 0

        for rec in records:
            try:
                # NSE API field names can vary; try common variations
                symbol = str(
                    rec.get("symbol")
                    or rec.get("nsecode")
                    or ""
                ).strip().upper()

                # Filter to universe
                if known_symbols and symbol not in known_symbols:
                    no_universe_match += 1
                    continue

                if not symbol:
                    symbol = "UNKNOWN"

                # NSE API uses 'desc' for category and 'attchmntText' for full headline
                headline = (
                    rec.get("attchmntText")
                    or rec.get("desc")
                    or rec.get("subject")
                    or rec.get("headline")
                    or "No headline"
                ).strip()[:500]

                # Source URL: prefer attachment file link
                attchmt = rec.get("attchmntFile", "") or rec.get("attchmt", "")
                if attchmt:
                    source_url = attchmt
                else:
                    seq_id = rec.get("seq_id", "")
                    source_url = (
                        f"https://www.nseindia.com/corporate/announcements/{seq_id}"
                        if seq_id else url
                    )

                if source_url in existing_urls:
                    skipped += 1
                    continue

                # NSE API uses 'an_dt' or 'sort_date' for the announcement timestamp
                bcast_date = (
                    rec.get("an_dt")
                    or rec.get("sort_date")
                    or rec.get("bcastDttm")
                    or rec.get("date")
                )
                event_ts   = _parse_nse_timestamp(bcast_date)

                # Extract a brief text snippet
                raw_text = rec.get("desc") or rec.get("subject") or headline

                event = Event(
                    symbol          = symbol,
                    source          = "nse_announcement",
                    source_url      = source_url,
                    event_timestamp = event_ts,
                    headline        = headline,
                    raw_text        = str(raw_text)[:5000] if raw_text else None,
                    category        = None,    # AI fills this in Milestone 4
                    sentiment       = None,    # placeholder
                    priority_label  = None,    # placeholder
                    catalyst_score  = 0.0,     # placeholder
                )
                db.add(event)
                existing_urls.add(source_url)
                inserted += 1

            except Exception as exc:
                logger.error("Error processing announcement record: %s — %s", rec, exc)
                errors += 1
                continue

        db.commit()

        result = {
            "universe":           universe,
            "since_hours":        since_hours,
            "api_records":        len(records),
            "no_universe_match":  no_universe_match,
            "inserted":           inserted,
            "skipped":            skipped,
            "errors":             errors,
        }
        logger.info("Announcements collector complete: %s", result)
        return result

    finally:
        if own_session:
            db.close()
