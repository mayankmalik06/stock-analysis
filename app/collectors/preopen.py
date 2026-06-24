"""
app/collectors/preopen.py

Collects NSE pre-open market data during the 9:00–9:15 am IST session.

MILESTONE: 2 (Source Ingestion)
STATUS: Stub — implementation coming in Milestone 2.

Planned behaviour:
- Poll NSE pre-open market data endpoint repeatedly (every ~2 minutes).
- Extract indicative_price, prev_close, gap_pct, buy_qty, sell_qty,
  indicative_volume, indicative_value for all active Nifty 500 symbols.
- Save each poll as a separate row in preopen_snapshots.
- Stop polling after 9:15 am IST.
"""


def fetch_preopen_snapshot() -> list:
    """
    Fetch a single pre-open snapshot from NSE for all active symbols.
    Call this repeatedly during 9:00–9:15 am IST.
    """
    raise NotImplementedError("Pre-open collector will be implemented in Milestone 2.")
