"""
app/collectors/universe.py

Loads and refreshes the Nifty 500 constituent universe from NSE.

MILESTONE: 2 (Source Ingestion)
STATUS: Stub — implementation coming in Milestone 2.

Planned behaviour:
- Download the official Nifty 500 CSV from NSE archives.
- Parse symbol, company name, series, sector.
- Upsert records into the symbols table.
- Mark stale symbols as is_active=False.
"""


def load_nifty500_universe():
    """Fetch Nifty 500 constituents from NSE and update the symbols table."""
    raise NotImplementedError("Universe loader will be implemented in Milestone 2.")
