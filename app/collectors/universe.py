"""
app/collectors/universe.py

Two responsibilities:
  1. load_nifty500()  — Downloads the official Nifty 500 constituent CSV from
                        NSE and upserts rows into the symbols table, setting
                        in_nifty_500 = True for every member.

  2. get_universe_symbols() — Returns a list of ticker strings for a chosen
                        universe filter.  Used by every other collector so they
                        all respect the same universe selection logic.

Universe filter values (string):
    "nifty_500"        → symbols where in_nifty_500 = True   (default)
    "nifty_50"         → symbols where in_nifty_50  = True
    "custom_watchlist" → symbols where is_custom_watchlist = True
    "all"              → all active symbols (no filter)

How to run:
    python scripts/load_nifty500.py
or via FastAPI:
    POST /collectors/load-nifty500
"""

import logging
import os
import sys
import io

import httpx
import pandas as pd
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Symbol

logger = logging.getLogger(__name__)

# ── NSE Nifty 500 CSV URL ─────────────────────────────────────────────────────
# NSE publishes the constituent list as a downloadable CSV at this path.
# If NSE changes the URL, update this constant.
NIFTY500_CSV_URL = (
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
)

# Fallback list of a few well-known Nifty 500 stocks used when the live NSE
# download is not reachable (e.g. running outside market hours or from a
# sandboxed environment).
FALLBACK_SYMBOLS = [
    {"symbol": "RELIANCE",  "company_name": "Reliance Industries Ltd",           "series": "EQ", "sector": "Oil Gas & Consumable Fuels"},
    {"symbol": "TCS",       "company_name": "Tata Consultancy Services Ltd",      "series": "EQ", "sector": "Information Technology"},
    {"symbol": "HDFCBANK",  "company_name": "HDFC Bank Ltd",                      "series": "EQ", "sector": "Financial Services"},
    {"symbol": "INFY",      "company_name": "Infosys Ltd",                        "series": "EQ", "sector": "Information Technology"},
    {"symbol": "ICICIBANK", "company_name": "ICICI Bank Ltd",                     "series": "EQ", "sector": "Financial Services"},
    {"symbol": "HINDUNILVR","company_name": "Hindustan Unilever Ltd",             "series": "EQ", "sector": "Fast Moving Consumer Goods"},
    {"symbol": "SBIN",      "company_name": "State Bank of India",                "series": "EQ", "sector": "Financial Services"},
    {"symbol": "BHARTIARTL","company_name": "Bharti Airtel Ltd",                  "series": "EQ", "sector": "Telecommunication"},
    {"symbol": "KOTAKBANK", "company_name": "Kotak Mahindra Bank Ltd",            "series": "EQ", "sector": "Financial Services"},
    {"symbol": "WIPRO",     "company_name": "Wipro Ltd",                          "series": "EQ", "sector": "Information Technology"},
    {"symbol": "LT",        "company_name": "Larsen & Toubro Ltd",                "series": "EQ", "sector": "Capital Goods"},
    {"symbol": "AXISBANK",  "company_name": "Axis Bank Ltd",                      "series": "EQ", "sector": "Financial Services"},
    {"symbol": "ASIANPAINT","company_name": "Asian Paints Ltd",                   "series": "EQ", "sector": "Consumer Durables"},
    {"symbol": "MARUTI",    "company_name": "Maruti Suzuki India Ltd",            "series": "EQ", "sector": "Automobile and Auto Components"},
    {"symbol": "SUNPHARMA", "company_name": "Sun Pharmaceutical Industries Ltd",  "series": "EQ", "sector": "Healthcare"},
    {"symbol": "TATAMOTORS","company_name": "Tata Motors Ltd",                    "series": "EQ", "sector": "Automobile and Auto Components"},
    {"symbol": "ULTRACEMCO","company_name": "UltraTech Cement Ltd",              "series": "EQ", "sector": "Construction Materials"},
    {"symbol": "BAJFINANCE","company_name": "Bajaj Finance Ltd",                  "series": "EQ", "sector": "Financial Services"},
    {"symbol": "NESTLEIND", "company_name": "Nestle India Ltd",                   "series": "EQ", "sector": "Fast Moving Consumer Goods"},
    {"symbol": "POWERGRID", "company_name": "Power Grid Corporation of India Ltd","series": "EQ", "sector": "Power"},
]


# ── 1. Nifty 500 loader ───────────────────────────────────────────────────────

def _download_nifty500_df() -> pd.DataFrame:
    """
    Try to download the live NSE Nifty 500 CSV.
    Returns a DataFrame with columns: symbol, company_name, series, sector.
    Falls back to the hardcoded FALLBACK_SYMBOLS list if download fails.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.nseindia.com/",
    }

    try:
        logger.info("Downloading Nifty 500 CSV from NSE: %s", NIFTY500_CSV_URL)
        response = httpx.get(NIFTY500_CSV_URL, headers=headers, timeout=30, follow_redirects=True)
        response.raise_for_status()

        df = pd.read_csv(io.StringIO(response.text))
        logger.info("Downloaded %d rows from NSE", len(df))

        # NSE CSV columns vary; normalise them
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        # Map NSE column names → our column names
        col_map = {}
        for c in df.columns:
            if "symbol" in c:
                col_map[c] = "symbol"
            elif "company" in c or "name" in c:
                col_map[c] = "company_name"
            elif "series" in c:
                col_map[c] = "series"
            elif "sector" in c or "industry" in c:
                col_map[c] = "sector"

        df = df.rename(columns=col_map)

        # Keep only the columns we care about
        keep = [c for c in ["symbol", "company_name", "series", "sector"] if c in df.columns]
        df = df[keep].copy()

        # Fill missing columns with defaults
        for col in ["symbol", "company_name", "series", "sector"]:
            if col not in df.columns:
                df[col] = None

        df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
        df["company_name"] = df["company_name"].astype(str).str.strip()
        df = df[df["symbol"].str.len() > 0]

        return df

    except Exception as exc:
        logger.warning(
            "Could not download live NSE Nifty 500 CSV (%s). "
            "Using fallback list of %d symbols.",
            exc, len(FALLBACK_SYMBOLS)
        )
        return pd.DataFrame(FALLBACK_SYMBOLS)


def load_nifty500(db: Session | None = None) -> dict:
    """
    Download the Nifty 500 constituent list from NSE and upsert into
    the symbols table.  Sets in_nifty_500 = True for every member and
    in_nifty_500 = False for any previously-marked symbol not in this run.

    Returns a dict with counts: inserted, updated, deactivated, total.

    Called from:
        scripts/load_nifty500.py
        POST /collectors/load-nifty500
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        df = _download_nifty500_df()
        live_symbols = set(df["symbol"].tolist())

        inserted = 0
        updated = 0

        for _, row in df.iterrows():
            sym = row["symbol"]
            existing = db.query(Symbol).filter(Symbol.symbol == sym).first()

            if existing is None:
                new_row = Symbol(
                    symbol=sym,
                    company_name=row.get("company_name") or sym,
                    series=row.get("series") or "EQ",
                    sector=row.get("sector") or None,
                    is_active=True,
                    in_nifty_500=True,
                    in_nifty_50=False,
                    is_custom_watchlist=False,
                )
                db.add(new_row)
                inserted += 1
            else:
                existing.company_name = row.get("company_name") or existing.company_name
                existing.series      = row.get("series")       or existing.series
                existing.sector      = row.get("sector")       or existing.sector
                existing.in_nifty_500 = True
                existing.is_active    = True
                updated += 1

        # Mark any previously-flagged symbols that are NOT in the current list
        deactivated = 0
        stale = (
            db.query(Symbol)
            .filter(Symbol.in_nifty_500 == True, Symbol.symbol.notin_(live_symbols))
            .all()
        )
        for s in stale:
            s.in_nifty_500 = False
            deactivated += 1

        db.commit()

        result = {
            "source":      "live_nse" if len(df) > len(FALLBACK_SYMBOLS) else "fallback",
            "total_in_csv": len(df),
            "inserted":    inserted,
            "updated":     updated,
            "deactivated": deactivated,
        }
        logger.info("Nifty 500 load complete: %s", result)
        return result

    finally:
        if own_session:
            db.close()


# ── 2. Universe filter helper ─────────────────────────────────────────────────

def get_universe_symbols(
    universe: str = "nifty_500",
    db: Session | None = None,
) -> list[str]:
    """
    Return a list of NSE ticker symbols for the requested universe.

    universe options:
        "nifty_500"        → symbols with in_nifty_500 = True  (default)
        "nifty_50"         → symbols with in_nifty_50  = True
        "custom_watchlist" → symbols with is_custom_watchlist = True
        "all"              → all is_active symbols

    Used by: nse_rss.py, nse_announcements.py, preopen.py
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        q = db.query(Symbol.symbol).filter(Symbol.is_active == True)

        if universe == "nifty_500":
            q = q.filter(Symbol.in_nifty_500 == True)
        elif universe == "nifty_50":
            q = q.filter(Symbol.in_nifty_50 == True)
        elif universe == "custom_watchlist":
            q = q.filter(Symbol.is_custom_watchlist == True)
        elif universe == "all":
            pass  # no additional filter
        else:
            logger.warning("Unknown universe '%s', defaulting to nifty_500", universe)
            q = q.filter(Symbol.in_nifty_500 == True)

        rows = q.all()
        symbols = [r.symbol for r in rows]
        logger.info("Universe '%s' → %d symbols", universe, len(symbols))
        return symbols

    finally:
        if own_session:
            db.close()


# ── 3. Custom watchlist loader ────────────────────────────────────────────────

def load_custom_watchlist(
    csv_path: str = "data/custom_watchlist.csv",
    db: Session | None = None,
) -> dict:
    """
    Reads a CSV file with a 'symbol' column and marks those symbols as
    is_custom_watchlist = True in the symbols table.

    The CSV must contain at least a 'symbol' column.
    Example file: data/custom_watchlist.csv

    Symbols in the CSV that don't already exist in the table are inserted
    with is_active = True so they can still be collected.

    Returns counts of marked, inserted, not_found.
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        if not os.path.exists(csv_path):
            return {"error": f"File not found: {csv_path}", "marked": 0}

        df = pd.read_csv(csv_path)
        df.columns = [c.strip().lower() for c in df.columns]

        if "symbol" not in df.columns:
            return {"error": "CSV must have a 'symbol' column", "marked": 0}

        watchlist_symbols = df["symbol"].astype(str).str.strip().str.upper().tolist()

        # Clear previous custom watchlist flags
        db.query(Symbol).filter(Symbol.is_custom_watchlist == True).update(
            {"is_custom_watchlist": False}
        )

        marked = 0
        inserted = 0

        for sym in watchlist_symbols:
            existing = db.query(Symbol).filter(Symbol.symbol == sym).first()
            if existing:
                existing.is_custom_watchlist = True
                marked += 1
            else:
                # Symbol not in DB yet — insert a minimal row
                new_row = Symbol(
                    symbol=sym,
                    company_name=sym,
                    is_active=True,
                    is_custom_watchlist=True,
                )
                db.add(new_row)
                inserted += 1
                marked += 1

        db.commit()
        result = {"marked": marked, "inserted": inserted}
        logger.info("Custom watchlist load complete: %s", result)
        return result

    finally:
        if own_session:
            db.close()
