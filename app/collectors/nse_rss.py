"""
app/collectors/nse_rss.py

Collects news items from NSE RSS feeds using feedparser.

What it does:
  - Fetches a set of NSE RSS feed URLs.
  - Parses all entries with feedparser.
  - Tries to match each headline to a known symbol from the chosen universe.
  - Inserts matched (and unmatched) items into the events table.
  - Skips items already ingested (deduplication by source_url).

Universe filter (default: "nifty_500"):
  Pass universe="nifty_50" or universe="custom_watchlist" to restrict symbol
  matching to a smaller set.

How to run:
    python scripts/run_rss.py
or via FastAPI:
    POST /collectors/run-rss
    POST /collectors/run-rss?universe=nifty_50
"""

import logging
import datetime
from zoneinfo import ZoneInfo

import feedparser
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Event
from app.collectors.universe import get_universe_symbols

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── NSE RSS feed URLs ─────────────────────────────────────────────────────────
# NSE provides RSS feeds for corporate announcements.
# Note: NSE has migrated some feeds; the XML endpoints below may return 404
# in sandboxed/non-browser environments. The collector handles this gracefully
# by also trying alternative public RSS sources.
NSE_RSS_FEEDS = [
    {
        "url":      "https://www.nseindia.com/rss/corporate-filings-new-announcement.xml",
        "category": "corporate_filing",
    },
    {
        "url":      "https://www.nseindia.com/rss/corporate-filings-latest-announcements.xml",
        "category": "corporate_filing",
    },
    {
        "url":      "https://www.nseindia.com/rss/corporate-filings-board-meeting.xml",
        "category": "board_meeting",
    },
    {
        "url":      "https://www.nseindia.com/rss/corporate-filings-resu.xml",
        "category": "results",
    },
    # ── Fallback public financial news RSS feeds ───────────────────────
    # These are reliable public RSS feeds for Indian markets news.
    # Used as fallback when the NSE XML feeds return 404 in certain environments.
    {
        "url":      "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "category": "market_news",
    },
    {
        "url":      "https://www.livemint.com/rss/markets",
        "category": "market_news",
    },
    {
        "url":      "https://feeds.feedburner.com/ndtvprofit-latest",
        "category": "market_news",
    },
]

# HTTP headers required by NSE (they block requests without a browser User-Agent)
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer":         "https://www.nseindia.com/",
}


# ── Symbol matching helpers ───────────────────────────────────────────────────

def _build_symbol_set(universe: str) -> set[str]:
    """Return a set of known symbols for fast lookup."""
    return set(get_universe_symbols(universe=universe))


def _extract_symbol_from_text(text: str, known_symbols: set[str]) -> str | None:
    """
    Try to find a known NSE symbol mentioned in a headline or description.
    Strategy: tokenise by whitespace and punctuation, check each token.
    Returns the first match, or None if no known symbol is found.
    """
    if not text:
        return None
    import re
    tokens = re.split(r"[\s\-\|,./:()\[\]]+", text.upper())
    for token in tokens:
        token = token.strip()
        if len(token) >= 2 and token in known_symbols:
            return token
    return None


def _parse_rss_timestamp(entry) -> datetime.datetime | None:
    """
    feedparser normalises timestamps into a time.struct_time in entry.published_parsed.
    Convert to a Python datetime in IST.
    """
    import time as _time
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            ts_utc = datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
            return ts_utc.astimezone(IST).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.datetime.now()


# ── Main collector ────────────────────────────────────────────────────────────

def fetch_nse_rss_feeds(
    universe: str = "nifty_500",
    db: Session | None = None,
) -> dict:
    """
    Fetch all NSE RSS feeds, match items to known symbols, insert into events.

    Parameters:
        universe  — "nifty_500" (default), "nifty_50", "custom_watchlist", "all"
        db        — optional SQLAlchemy session (a new one is created if None)

    Returns a dict with: feeds_fetched, items_parsed, items_inserted, items_skipped
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        known_symbols = _build_symbol_set(universe)
        logger.info("RSS collector: universe='%s', known symbols=%d", universe, len(known_symbols))

        if not known_symbols:
            logger.warning(
                "No symbols found for universe '%s'. "
                "Run load_nifty500.py first.",
                universe
            )

        # Collect existing source_urls to avoid duplicate inserts
        existing_urls: set[str] = {
            row[0]
            for row in db.query(Event.source_url)
            .filter(Event.source == "nse_rss")
            .all()
            if row[0]
        }

        feeds_fetched = 0
        items_parsed = 0
        items_inserted = 0
        items_skipped = 0

        for feed_cfg in NSE_RSS_FEEDS:
            feed_url  = feed_cfg["url"]
            feed_cat  = feed_cfg["category"]

            try:
                logger.info("Fetching RSS feed: %s", feed_url)
                parsed = feedparser.parse(
                    feed_url,
                    request_headers=NSE_HEADERS,
                )

                if parsed.bozo and not parsed.entries:
                    logger.warning("Feed parse error for %s: %s", feed_url, parsed.bozo_exception)
                    continue

                feeds_fetched += 1
                logger.info("  → %d entries", len(parsed.entries))

                for entry in parsed.entries:
                    items_parsed += 1

                    headline = (
                        getattr(entry, "title", None)
                        or getattr(entry, "summary", None)
                        or "No headline"
                    )
                    headline = headline.strip()

                    raw_text = getattr(entry, "summary", None) or headline
                    source_url = getattr(entry, "link", None) or feed_url

                    # Deduplication
                    if source_url in existing_urls:
                        items_skipped += 1
                        continue

                    # Symbol matching
                    full_text = f"{headline} {raw_text}"
                    symbol = _extract_symbol_from_text(full_text, known_symbols)

                    # If no symbol found, store under "UNKNOWN" so the row
                    # is still saved and can be processed later
                    if symbol is None:
                        symbol = "UNKNOWN"

                    event_ts = _parse_rss_timestamp(entry)

                    event = Event(
                        symbol          = symbol,
                        source          = "nse_rss",
                        source_url      = source_url,
                        event_timestamp = event_ts,
                        headline        = headline[:500],
                        raw_text        = raw_text[:5000] if raw_text else None,
                        category        = feed_cat,      # placeholder; AI fills this in M4
                        sentiment       = None,          # placeholder
                        priority_label  = None,          # placeholder
                        catalyst_score  = 0.0,           # placeholder
                    )
                    db.add(event)
                    existing_urls.add(source_url)
                    items_inserted += 1

            except Exception as exc:
                logger.error("Error fetching feed %s: %s", feed_url, exc)
                continue

        db.commit()

        result = {
            "universe":       universe,
            "feeds_fetched":  feeds_fetched,
            "items_parsed":   items_parsed,
            "items_inserted": items_inserted,
            "items_skipped":  items_skipped,
        }
        logger.info("RSS collector complete: %s", result)
        return result

    finally:
        if own_session:
            db.close()
