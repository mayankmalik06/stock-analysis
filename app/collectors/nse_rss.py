"""
app/collectors/nse_rss.py

Collects news items from NSE RSS feeds using feedparser.

MILESTONE: 2 (Source Ingestion)
STATUS: Stub — implementation coming in Milestone 2.

Planned behaviour:
- Fetch NSE RSS feed URLs.
- Parse feed entries with feedparser.
- Map entries to Nifty 500 symbols where possible.
- Store new items in the events table.
"""


def fetch_nse_rss_feeds() -> list:
    """Fetch and parse NSE RSS feed entries."""
    raise NotImplementedError("NSE RSS collector will be implemented in Milestone 2.")
