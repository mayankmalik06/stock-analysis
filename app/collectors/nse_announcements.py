"""
app/collectors/nse_announcements.py

Collects corporate announcements from NSE filing pages.

MILESTONE: 2 (Source Ingestion)
STATUS: Stub — implementation coming in Milestone 2.

Planned behaviour:
- Poll NSE corporate announcement endpoints (official source).
- Parse headlines, timestamps, symbols, and source URLs.
- Deduplicate against already-ingested events.
- Store raw rows in the events table for later AI classification.
"""


def fetch_nse_announcements(since_hours: int = 24) -> list:
    """Fetch NSE corporate announcements published in the last N hours."""
    raise NotImplementedError("NSE announcement collector will be implemented in Milestone 2.")
