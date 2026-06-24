"""
app/collectors/preopen.py

Collects NSE pre-open market data during the 9:00–9:15 am IST session.

What it does:
  - Hits the NSE /api/market-data-pre-open?key=ALL endpoint.
  - Extracts indicative_price, prev_close, gap_pct, buy_qty, sell_qty,
    indicative_volume, indicative_value for every symbol.
  - Filters to the chosen universe.
  - Saves one row per symbol per poll into preopen_snapshots.

Test mode:
  - Runs a single poll right now, regardless of time-of-day.
  - Uses live NSE data if available, or a hardcoded mock dataset if not.
  - Useful for verifying the pipeline outside 9:00–9:15 am IST.

How to run:
    python scripts/run_preopen.py --test              (single poll, any time)
    python scripts/run_preopen.py                     (live mode, time-gated)
or via FastAPI:
    POST /collectors/run-preopen?test_mode=true
    POST /collectors/run-preopen?universe=nifty_50
"""

import logging
import datetime
import time
from zoneinfo import ZoneInfo

import requests
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import PreopenSnapshot
from app.collectors.universe import get_universe_symbols

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

# Pre-open session window (IST, no timezone info — compare with naive datetime)
PREOPEN_START = datetime.time(9, 0)
PREOPEN_END   = datetime.time(9, 15)


# ── Mock data for test mode ───────────────────────────────────────────────────
# Used when the live NSE endpoint is not reachable (e.g. outside market hours,
# sandboxed environment, or NSE blocking the request).
MOCK_PREOPEN_DATA = [
    {"symbol": "RELIANCE",   "iep": 2950.50, "previousClose": 2900.00, "totalTradedVolume": 320000, "totalBuyQuantity": 180000, "totalSellQuantity": 140000},
    {"symbol": "TCS",        "iep": 3890.00, "previousClose": 3850.00, "totalTradedVolume": 145000, "totalBuyQuantity":  90000, "totalSellQuantity":  55000},
    {"symbol": "HDFCBANK",   "iep": 1720.25, "previousClose": 1700.00, "totalTradedVolume": 280000, "totalBuyQuantity": 160000, "totalSellQuantity": 120000},
    {"symbol": "INFY",       "iep": 1580.00, "previousClose": 1610.00, "totalTradedVolume": 210000, "totalBuyQuantity":  95000, "totalSellQuantity": 115000},
    {"symbol": "ICICIBANK",  "iep": 1240.00, "previousClose": 1220.00, "totalTradedVolume": 350000, "totalBuyQuantity": 200000, "totalSellQuantity": 150000},
    {"symbol": "SBIN",       "iep":  845.50, "previousClose":  830.00, "totalTradedVolume": 520000, "totalBuyQuantity": 310000, "totalSellQuantity": 210000},
    {"symbol": "BHARTIARTL", "iep": 1680.00, "previousClose": 1650.00, "totalTradedVolume": 190000, "totalBuyQuantity": 110000, "totalSellQuantity":  80000},
    {"symbol": "WIPRO",      "iep":  530.00, "previousClose":  545.00, "totalTradedVolume": 175000, "totalBuyQuantity":  80000, "totalSellQuantity":  95000},
    {"symbol": "LT",         "iep": 3600.00, "previousClose": 3550.00, "totalTradedVolume": 125000, "totalBuyQuantity":  75000, "totalSellQuantity":  50000},
    {"symbol": "AXISBANK",   "iep": 1195.00, "previousClose": 1180.00, "totalTradedVolume": 295000, "totalBuyQuantity": 165000, "totalSellQuantity": 130000},
]


# ── NSE session helper ─────────────────────────────────────────────────────────

def _get_nse_session() -> requests.Session:
    """Create a requests session with NSE cookies pre-seeded."""
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get(NSE_HOMEPAGE, timeout=15)
        time.sleep(1)
    except Exception as exc:
        logger.warning("Could not pre-seed NSE session: %s", exc)
    return session


# ── Single poll ────────────────────────────────────────────────────────────────

def _fetch_single_poll(nse_session: requests.Session) -> list[dict]:
    """
    Hit the NSE pre-open endpoint once and return the raw data list.
    Returns an empty list on any error.
    """
    try:
        response = nse_session.get(NSE_PREOPEN_URL, timeout=20)
        response.raise_for_status()
        data = response.json()

        # NSE returns {"data": [...], "timestamp": "..."} structure
        if isinstance(data, dict):
            records = data.get("data", [])
        elif isinstance(data, list):
            records = data
        else:
            records = []

        logger.info("Pre-open API returned %d records", len(records))
        return records

    except Exception as exc:
        logger.warning("Pre-open API call failed: %s", exc)
        return []


def _parse_record(rec: dict) -> dict | None:
    """
    Extract fields from a single NSE pre-open record.
    NSE field names: symbol, iep (indicative equilibrium price),
    previousClose, totalTradedVolume, totalBuyQuantity, totalSellQuantity.
    Returns None if the record can't be parsed.
    """
    try:
        # NSE wraps data in a nested "metadata" dict in some API versions
        meta = rec.get("metadata", rec)

        symbol       = str(meta.get("symbol", "")).strip().upper()
        if not symbol:
            return None

        iep          = float(meta.get("iep") or 0)
        prev_close   = float(meta.get("previousClose") or meta.get("prevClose") or 0)
        buy_qty      = int(meta.get("totalBuyQuantity") or 0)
        sell_qty     = int(meta.get("totalSellQuantity") or 0)
        ind_volume   = int(meta.get("totalTradedVolume") or 0)

        gap_pct = (
            round((iep - prev_close) / prev_close * 100, 4)
            if prev_close > 0 else 0.0
        )
        ind_value = round(iep * ind_volume, 2) if iep and ind_volume else 0.0

        return {
            "symbol":            symbol,
            "indicative_price":  iep,
            "prev_close":        prev_close,
            "gap_pct":           gap_pct,
            "buy_qty":           buy_qty,
            "sell_qty":          sell_qty,
            "indicative_volume": ind_volume,
            "indicative_value":  ind_value,
        }
    except Exception as exc:
        logger.debug("Could not parse pre-open record %s: %s", rec, exc)
        return None


def _save_snapshots(
    records: list[dict],
    known_symbols: set[str],
    snapshot_time: datetime.datetime,
    db: Session,
) -> dict:
    """
    Save parsed pre-open records to preopen_snapshots.
    Only saves symbols in known_symbols (pass an empty set to save all).
    Returns counts of saved and skipped.
    """
    saved   = 0
    skipped = 0

    for rec in records:
        parsed = _parse_record(rec)
        if parsed is None:
            skipped += 1
            continue

        symbol = parsed["symbol"]

        # Universe filter
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


# ── Public interface ───────────────────────────────────────────────────────────

def fetch_preopen_snapshot(
    universe:  str = "nifty_500",
    test_mode: bool = False,
    db: Session | None = None,
) -> dict:
    """
    Fetch a single pre-open snapshot and store results in preopen_snapshots.

    Parameters:
        universe  — "nifty_500" (default), "nifty_50", "custom_watchlist", "all"
        test_mode — if True, always runs regardless of time; uses mock data
                    if the live NSE endpoint is not reachable
        db        — optional SQLAlchemy session

    Returns dict with: universe, test_mode, source, records_from_api,
                       saved, skipped, snapshot_time
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        now_ist = datetime.datetime.now(tz=IST).replace(tzinfo=None)

        # Time guard (skipped in test mode)
        if not test_mode:
            current_time = now_ist.time()
            if not (PREOPEN_START <= current_time <= PREOPEN_END):
                msg = (
                    f"Pre-open collector runs only between "
                    f"{PREOPEN_START} and {PREOPEN_END} IST. "
                    f"Current time: {current_time}. "
                    f"Use test_mode=True to run outside market hours."
                )
                logger.warning(msg)
                return {"skipped_reason": msg}

        known_symbols = set(get_universe_symbols(universe=universe))
        logger.info(
            "Pre-open collector: universe='%s', symbols=%d, test_mode=%s",
            universe, len(known_symbols), test_mode
        )

        snapshot_time = now_ist
        source        = "live_nse"

        # Try live NSE data
        nse_session = _get_nse_session()
        records     = _fetch_single_poll(nse_session)

        # Fall back to mock data if live call failed or returned nothing
        if not records:
            if test_mode:
                logger.info("Using mock pre-open data for test mode.")
                records = [
                    {
                        "metadata": {
                            "symbol":             r["symbol"],
                            "iep":                r["iep"],
                            "previousClose":      r["previousClose"],
                            "totalTradedVolume":  r["totalTradedVolume"],
                            "totalBuyQuantity":   r["totalBuyQuantity"],
                            "totalSellQuantity":  r["totalSellQuantity"],
                        }
                    }
                    for r in MOCK_PREOPEN_DATA
                ]
                source = "mock"
            else:
                return {
                    "universe":       universe,
                    "test_mode":      test_mode,
                    "source":         "none",
                    "records_from_api": 0,
                    "saved":          0,
                    "skipped":        0,
                    "error":          "NSE returned no data",
                }

        counts = _save_snapshots(records, known_symbols, snapshot_time, db)

        result = {
            "universe":         universe,
            "test_mode":        test_mode,
            "source":           source,
            "records_from_api": len(records),
            "saved":            counts["saved"],
            "skipped":          counts["skipped"],
            "snapshot_time":    snapshot_time.isoformat(),
        }
        logger.info("Pre-open collector complete: %s", result)
        return result

    finally:
        if own_session:
            db.close()


def run_preopen_session(
    universe:      str = "nifty_500",
    poll_interval: int = 120,
    db: Session | None = None,
) -> dict:
    """
    Run continuous polling for the full 9:00–9:15 am IST pre-open session.

    Polls every poll_interval seconds (default 120 = 2 minutes).
    Stops automatically at 9:15 am IST.

    Parameters:
        universe       — universe filter
        poll_interval  — seconds between polls (default 120)

    Returns dict with total polls and total snapshots saved.
    """
    total_polls   = 0
    total_saved   = 0

    logger.info(
        "Starting pre-open session polling: universe='%s', interval=%ds",
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
                datetime.datetime.combine(now_ist.date(), PREOPEN_START)
                - now_ist.replace(tzinfo=None)
            ).total_seconds()
            logger.info("Pre-open not started yet. Waiting %.0f seconds.", wait_secs)
            time.sleep(min(wait_secs, 30))
            continue

        result = fetch_preopen_snapshot(universe=universe, test_mode=False, db=db)
        total_polls += 1
        total_saved += result.get("saved", 0)

        logger.info(
            "Poll %d complete: %d saved, %d skipped",
            total_polls, result.get("saved", 0), result.get("skipped", 0)
        )

        # Wait before next poll
        time.sleep(poll_interval)

    return {"total_polls": total_polls, "total_saved": total_saved}
