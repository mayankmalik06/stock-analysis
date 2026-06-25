"""
app/collectors/nse_announcements.py

Milestone 6: Live NSE corporate announcements collector.

Fetches real NSE announcements for a specific trade_date and stores them
in the symbol_events table as PENDING classification (event_type = NULL).
The classify_events script then runs the LLM classifier on those rows.

Two feed sources are tried in order:
  1. NSE Online Announcements RSS feed
     https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml
  2. NSE Corporate Announcements JSON API (date-filtered)
     https://www.nseindia.com/api/corporate-announcements?index=equities&...

The collector:
  - Filters items to symbols in the requested universe.
  - Marks items with symbol = "UNMAPPED" if the NSE symbol cannot be
    resolved (so nothing is silently dropped).
  - Deduplicates by raw_text_hash so re-running is safe.
  - Returns a plain summary dict — no exceptions raised to the caller.

Usage:
    from app.collectors.nse_announcements import fetch_announcements_for_date
    result = fetch_announcements_for_date(trade_date=date(2026, 6, 26))
"""

import logging
import datetime
import time
import hashlib
import re
from typing import Optional

import requests
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

NSE_RSS_URL = (
    "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
)

NSE_ANNOUNCE_API = (
    "https://www.nseindia.com/api/corporate-announcements"
    "?index=equities&from_date={from_date}&to_date={to_date}&offset=0&limit=100"
)

NSE_HOMEPAGE = "https://www.nseindia.com"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}

# How many seconds to wait between retry attempts
RETRY_DELAYS = [2, 5, 10]

SOURCE_TAG = "NSE_ANNOUNCEMENTS"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hash_text(text: str) -> str:
    """SHA-256 hash of text, truncated to 64 chars — used for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:64]


def _get_nse_session() -> requests.Session:
    """
    Create a requests.Session, visit the NSE homepage to obtain the required
    session cookies, then return the ready session.
    """
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    for attempt in range(3):
        try:
            resp = session.get(NSE_HOMEPAGE, timeout=15)
            resp.raise_for_status()
            logger.debug("NSE homepage seeded cookies (attempt %d).", attempt + 1)
            time.sleep(1)
            return session
        except Exception as exc:
            logger.warning(
                "NSE homepage seed attempt %d failed: %s", attempt + 1, exc
            )
            if attempt < 2:
                time.sleep(RETRY_DELAYS[attempt])
    logger.warning("Could not seed NSE session cookies after 3 attempts.")
    return session


def _parse_nse_timestamp(ts_str: Optional[str]) -> Optional[datetime.datetime]:
    """
    Parse NSE timestamp strings into datetime objects.
    NSE uses several formats; tries the most common ones.
    Returns None if unparseable.
    """
    if not ts_str:
        return None
    formats = [
        "%d-%b-%Y %H:%M:%S",  # 25-Jun-2026 09:30:00
        "%d-%b-%Y",           # 25-Jun-2026
        "%Y-%m-%dT%H:%M:%S",  # ISO 8601
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(ts_str.strip(), fmt)
        except ValueError:
            continue
    logger.debug("Could not parse NSE timestamp string: %r", ts_str)
    return None


def _symbol_from_rss_title(title: str) -> str:
    """
    Try to extract an NSE symbol from an RSS item title.
    NSE RSS titles often follow patterns like:
      "RELIANCE - Board Meeting"
      "INFY : Quarterly Results"
      "[HDFCBANK] Dividend Announcement"
    Returns uppercase symbol or "" if not found.
    """
    # Pattern 1: starts with CAPS before a dash or colon
    m = re.match(r"^\[?([A-Z][A-Z0-9&]{1,19})\]?\s*[-:]", title)
    if m:
        return m.group(1).strip()
    # Pattern 2: all-caps word at the very start
    m = re.match(r"^([A-Z][A-Z0-9&]{1,19})\s", title)
    if m:
        return m.group(1).strip()
    return ""


def _get_universe_symbols(universe: str) -> set:
    """Load known NSE symbols for the requested universe (for filtering)."""
    from app.collectors.universe import get_universe_symbols
    return set(get_universe_symbols(universe=universe))


# ── RSS feed collector ─────────────────────────────────────────────────────────

def _fetch_rss_items(
    trade_date: datetime.date,
    session: requests.Session,
) -> list[dict]:
    """
    Fetch the NSE Online Announcements RSS feed and parse items.
    Filters to items whose published date matches trade_date (or the previous
    evening, i.e. from 6pm the day before).

    Returns a list of normalised dicts:
        symbol, headline, body, announced_at (datetime or None), source_url
    """
    try:
        import feedparser
    except ImportError:
        logger.error(
            "feedparser is not installed. Run: pip install feedparser"
        )
        return []

    items = []
    for attempt in range(3):
        try:
            resp = session.get(NSE_RSS_URL, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
            break
        except Exception as exc:
            logger.warning(
                "RSS fetch attempt %d failed: %s", attempt + 1, exc
            )
            if attempt < 2:
                time.sleep(RETRY_DELAYS[attempt])
    else:
        logger.error("RSS feed unreachable after 3 attempts.")
        return []

    # Window: from 6pm the previous evening to 11:59pm on trade_date
    window_start = datetime.datetime.combine(
        trade_date - datetime.timedelta(days=1),
        datetime.time(18, 0),
    )
    window_end = datetime.datetime.combine(
        trade_date,
        datetime.time(23, 59, 59),
    )

    for entry in feed.entries:
        title = getattr(entry, "title", "") or ""
        summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        link = getattr(entry, "link", "") or ""

        # Parse published timestamp
        published_parsed = getattr(entry, "published_parsed", None)
        if published_parsed:
            try:
                announced_at = datetime.datetime(*published_parsed[:6])
            except Exception:
                announced_at = None
        else:
            announced_at = None

        # Filter by time window
        if announced_at:
            if not (window_start <= announced_at <= window_end):
                continue
        # If no timestamp, include anyway (better to classify than drop)

        # Try to extract symbol from title
        symbol = _symbol_from_rss_title(title)

        # Build the body text that will be sent to the LLM classifier
        body = summary.strip() if summary else title.strip()

        items.append({
            "symbol": symbol.upper() if symbol else "",
            "headline": title.strip()[:500],
            "body": body[:2000],
            "announced_at": announced_at,
            "source_url": link,
        })

    logger.info("RSS feed: %d items in time window.", len(items))
    return items


# ── JSON API collector ─────────────────────────────────────────────────────────

def _fetch_json_api_items(
    trade_date: datetime.date,
    session: requests.Session,
) -> list[dict]:
    """
    Fetch the NSE Corporate Announcements JSON API for the given trade_date
    (and the day before, to catch evening filings).

    Returns the same normalised dict format as _fetch_rss_items().
    """
    # Include the previous day to pick up evening announcements
    from_dt = trade_date - datetime.timedelta(days=1)
    from_date = from_dt.strftime("%d-%m-%Y")
    to_date = trade_date.strftime("%d-%m-%Y")

    url = NSE_ANNOUNCE_API.format(from_date=from_date, to_date=to_date)
    logger.info("JSON API: fetching %s", url)

    records = []
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            # API returns list or {"data": [...]}
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                records = data.get("data", [])
            break
        except Exception as exc:
            logger.warning(
                "JSON API attempt %d failed: %s", attempt + 1, exc
            )
            if attempt < 2:
                time.sleep(RETRY_DELAYS[attempt])
    else:
        logger.error("JSON API unreachable after 3 attempts.")
        return []

    logger.info("JSON API: %d records returned.", len(records))

    items = []
    for rec in records:
        symbol = str(
            rec.get("symbol") or rec.get("nsecode") or ""
        ).strip().upper()

        headline = (
            rec.get("attchmntText")
            or rec.get("desc")
            or rec.get("subject")
            or rec.get("headline")
            or "No headline"
        ).strip()[:500]

        body = (rec.get("desc") or rec.get("subject") or headline).strip()

        attchmt = rec.get("attchmntFile") or rec.get("attchmt") or ""
        if attchmt:
            source_url = attchmt
        else:
            seq_id = rec.get("seq_id", "")
            source_url = (
                f"https://www.nseindia.com/corporate/announcements/{seq_id}"
                if seq_id else url
            )

        bcast_raw = (
            rec.get("an_dt")
            or rec.get("sort_date")
            or rec.get("bcastDttm")
            or rec.get("date")
        )
        announced_at = _parse_nse_timestamp(bcast_raw)

        items.append({
            "symbol": symbol,
            "headline": headline,
            "body": body[:2000],
            "announced_at": announced_at,
            "source_url": source_url,
        })

    return items


# ── Main public function ───────────────────────────────────────────────────────

def fetch_announcements_for_date(
    trade_date: datetime.date,
    universe: str = "nifty_500",
    db: Optional[Session] = None,
) -> dict:
    """
    Fetch NSE announcements for trade_date and store them in symbol_events
    as pending classification (event_type = NULL).

    The caller should run classify_events.py afterwards to classify the rows.

    Parameters
    ----------
    trade_date : date to fetch announcements for
    universe   : "nifty_500" (default), "nifty_50", "custom_watchlist", "all"
    db         : optional SQLAlchemy session (creates its own if not provided)

    Returns
    -------
    dict with:
        trade_date, universe, rss_items, api_items,
        inserted, skipped_duplicate, skipped_no_symbol_match,
        unmapped_stored, errors
    """
    from app.db import SessionLocal
    from app.models import SymbolEvent

    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        known_symbols = _get_universe_symbols(universe)
        logger.info(
            "Announcements collector | date=%s | universe=%s (%d symbols)",
            trade_date, universe, len(known_symbols),
        )

        # Get existing hashes for today to deduplicate without DB roundtrips
        existing_hashes: set[str] = {
            row[0]
            for row in db.query(SymbolEvent.raw_text_hash)
            .filter(SymbolEvent.trade_date == trade_date)
            .all()
        }

        nse_session = _get_nse_session()

        # --- Try RSS first, then JSON API --------------------------------
        rss_items = _fetch_rss_items(trade_date, nse_session)
        api_items = _fetch_json_api_items(trade_date, nse_session)

        # Merge: deduplicate by (symbol, headline) to avoid double-counting
        # items that appear in both feeds
        seen_headlines: set[str] = set()
        all_items: list[dict] = []

        for item in rss_items + api_items:
            key = (item["symbol"], item["headline"][:80])
            if key not in seen_headlines:
                seen_headlines.add(key)
                all_items.append(item)

        logger.info(
            "Total unique items from both feeds: %d (RSS=%d, API=%d)",
            len(all_items), len(rss_items), len(api_items),
        )

        inserted = 0
        skipped_duplicate = 0
        skipped_no_match = 0
        unmapped_stored = 0
        errors = 0

        for item in all_items:
            try:
                symbol = item["symbol"]
                headline = item["headline"]
                body = item["body"]
                announced_at = item["announced_at"]

                # Build raw_text that will be sent to the classifier
                if body and body != headline:
                    raw_text = f"{headline}\n\n{body}"
                else:
                    raw_text = headline

                # Filter / map symbol
                if known_symbols and symbol not in known_symbols:
                    if symbol:
                        # Known symbol just not in our universe — skip silently
                        skipped_no_match += 1
                        continue
                    else:
                        # Symbol could not be extracted from RSS title
                        # Store as UNMAPPED so nothing is silently dropped
                        symbol = "UNMAPPED"
                        unmapped_stored += 1
                        logger.debug(
                            "UNMAPPED item stored: %s", headline[:60]
                        )

                text_hash = _hash_text(raw_text)
                if text_hash in existing_hashes:
                    skipped_duplicate += 1
                    continue

                row = SymbolEvent(
                    trade_date=trade_date,
                    symbol=symbol,
                    raw_text=raw_text,
                    source=SOURCE_TAG,
                    headline=headline[:500],
                    announced_at=announced_at,
                    # event_type / sentiment / confidence / label left NULL
                    # — classify_events.py will fill them in
                    event_type=None,
                    sentiment=None,
                    confidence=None,
                    label=None,
                    raw_text_hash=text_hash,
                )
                db.add(row)
                existing_hashes.add(text_hash)
                inserted += 1

            except Exception as exc:
                logger.error("Error storing announcement item: %s — %s", item, exc)
                errors += 1
                continue

        db.commit()

        summary = {
            "trade_date": str(trade_date),
            "universe": universe,
            "rss_items": len(rss_items),
            "api_items": len(api_items),
            "unique_items": len(all_items),
            "inserted": inserted,
            "skipped_duplicate": skipped_duplicate,
            "skipped_no_symbol_match": skipped_no_match,
            "unmapped_stored": unmapped_stored,
            "errors": errors,
        }
        logger.info("Announcements fetch complete: %s", summary)
        return summary

    finally:
        if own_session:
            db.close()
