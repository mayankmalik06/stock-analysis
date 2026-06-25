"""
app/collectors/nse_bhavcopy.py

Milestone 5 — NSE EOD Bhavcopy downloader and parser.

What it does:
  - Downloads the NSE "sec_bhavdata_full" CSV for a given calendar date.
  - URL format: https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
  - Parses SYMBOL, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE for EQ series only.
  - Returns a list of dicts: {symbol, prev_high, prev_low, prev_close}.
  - Filters to the provided universe (pass an empty set to get all symbols).

HTTP behaviour:
  - Sets a browser-style User-Agent and Accept headers (NSE blocks plain requests).
  - First hits the NSE homepage to seed cookies, then downloads the CSV.
  - Retries up to MAX_RETRIES times on transient (5xx / network) errors.
  - Raises BhavcopyCopyError on hard failures (404, parse error, etc.).

Why this URL:
  The "sec_bhavdata_full_DDMMYYYY.csv" file is the simplest publicly available
  NSE CM (Capital Market) EOD bhavcopy. It has been stable since at least 2025
  and requires no authentication. The new-format zip
  (BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip) is also supported as a
  fallback if the simpler URL returns a non-200 response.

Columns used from the CSV:
    SYMBOL        → NSE ticker (strip whitespace)
    SERIES        → we only keep "EQ" rows
    HIGH_PRICE    → prev_high for the day
    LOW_PRICE     → prev_low for the day
    CLOSE_PRICE   → prev_close for the day

Not used but present: DATE1, PREV_CLOSE, OPEN_PRICE, LAST_PRICE, AVG_PRICE,
    TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
"""

import io
import logging
import time
import zipfile
import datetime
from typing import Optional

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# Primary URL: plain CSV, no unzipping needed
_SIMPLE_URL = (
    "https://nsearchives.nseindia.com/products/content/"
    "sec_bhavdata_full_{ddmmyyyy}.csv"
)

# Fallback URL: zipped new-format bhavcopy (post-July 2024)
_ZIP_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)

NSE_HOMEPAGE = "https://www.nseindia.com"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds between retries


# ── Custom exception ──────────────────────────────────────────────────────────

class BhavcopyCopyError(Exception):
    """Raised when the bhavcopy cannot be downloaded or parsed."""


# ── HTTP session helper ────────────────────────────────────────────────────────

def _make_nse_session() -> requests.Session:
    """
    Build a requests session with NSE cookies pre-seeded.

    NSE blocks plain GET requests without a valid session cookie.
    Hitting the homepage first sets the required cookies.
    """
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        resp = session.get(NSE_HOMEPAGE, timeout=15)
        resp.raise_for_status()
        logger.debug("NSE homepage seeded OK (status %s)", resp.status_code)
        time.sleep(1)  # brief pause — be polite to NSE
    except Exception as exc:
        logger.warning("Could not seed NSE session: %s — continuing anyway", exc)
    return session


# ── Download helpers ───────────────────────────────────────────────────────────

def _download_with_retry(session: requests.Session, url: str) -> bytes:
    """
    Download a URL with up to MAX_RETRIES retries on transient errors.

    Returns the raw bytes of the response body.
    Raises BhavcopyCopyError on a hard failure (non-200 after retries).
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                logger.info("Downloaded %s (%.1f KB)", url, len(resp.content) / 1024)
                return resp.content
            if resp.status_code == 404:
                raise BhavcopyCopyError(
                    f"Bhavcopy not found (404) at {url}. "
                    "The date may be a holiday or weekend."
                )
            logger.warning(
                "Attempt %d/%d: HTTP %s for %s",
                attempt, MAX_RETRIES, resp.status_code, url
            )
            last_exc = BhavcopyCopyError(f"HTTP {resp.status_code} from {url}")
        except BhavcopyCopyError:
            raise
        except Exception as exc:
            logger.warning("Attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
            last_exc = exc

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    raise BhavcopyCopyError(
        f"Failed to download after {MAX_RETRIES} attempts: {last_exc}"
    )


def _parse_csv_bytes(raw: bytes) -> pd.DataFrame:
    """
    Parse raw CSV bytes (possibly whitespace-padded) into a DataFrame.
    Strips all column names and string values of leading/trailing whitespace.
    """
    text = raw.decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(text), dtype=str)
    # Strip whitespace from column names
    df.columns = [c.strip() for c in df.columns]
    # Strip whitespace from all string cells
    df = df.apply(lambda col: col.str.strip() if col.dtype == object else col)
    return df


def _parse_zip_bytes(raw: bytes) -> pd.DataFrame:
    """
    Parse a zipped CSV (the new-format bhavcopy zip) into a DataFrame.
    Opens the first .csv file found inside the zip.
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise BhavcopyCopyError("No CSV file found inside bhavcopy zip.")
        with zf.open(csv_names[0]) as f:
            return _parse_csv_bytes(f.read())


# ── Column mapping for new-format zip ─────────────────────────────────────────

# The zip bhavcopy uses different column names from the simple CSV
_ZIP_COLUMN_MAP = {
    "TckrSymb": "SYMBOL",
    "SctySrs": "SERIES",
    "HghPric": "HIGH_PRICE",
    "LwPric": "LOW_PRICE",
    "ClsPric": "CLOSE_PRICE",
}


def _normalise_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise a raw bhavcopy DataFrame to a standard shape with columns:
        SYMBOL, SERIES, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE
    Handles both the simple CSV format and the new-format zip.
    """
    # Rename zip-format columns if present
    df = df.rename(columns=_ZIP_COLUMN_MAP)

    required = {"SYMBOL", "SERIES", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE"}
    missing = required - set(df.columns)
    if missing:
        raise BhavcopyCopyError(
            f"Bhavcopy CSV is missing expected columns: {missing}. "
            f"Columns found: {list(df.columns)}"
        )

    # Keep only EQ series (equity) rows
    df = df[df["SERIES"].str.upper() == "EQ"].copy()

    # Cast price columns to float
    for col in ("HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with any missing price
    df = df.dropna(subset=["HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE"])

    return df[["SYMBOL", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE"]].reset_index(drop=True)


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_bhavcopy(
    date: datetime.date,
    universe_symbols: set[str] | None = None,
) -> list[dict]:
    """
    Download and parse the NSE EOD bhavcopy for the given date.

    Parameters
    ----------
    date : datetime.date
        The trading session date whose OHLC data you want.
        For a pre-market run on 2026-06-26, pass 2026-06-25 (previous session).

    universe_symbols : set of str, optional
        If provided, filter results to only these NSE tickers.
        Pass None or an empty set to return all EQ-series symbols.

    Returns
    -------
    list of dict, each with keys:
        symbol      — NSE ticker (upper-case, no whitespace)
        prev_high   — session high
        prev_low    — session low
        prev_close  — session close

    Raises
    ------
    BhavcopyCopyError
        If the file cannot be downloaded (e.g. holiday) or parsed.
    """
    ddmmyyyy = date.strftime("%d%m%Y")
    yyyymmdd = date.strftime("%Y%m%d")

    simple_url = _SIMPLE_URL.format(ddmmyyyy=ddmmyyyy)
    zip_url = _ZIP_URL.format(yyyymmdd=yyyymmdd)

    # Try simple (plain CSV) URL first
    df: pd.DataFrame | None = None
    try:
        session = _make_nse_session()
        raw = _download_with_retry(session, simple_url)
        df = _normalise_df(_parse_csv_bytes(raw))
        logger.info("Bhavcopy loaded via simple URL: %d EQ rows", len(df))
    except BhavcopyCopyError as e:
        if "404" in str(e):
            logger.warning("Simple URL returned 404 — trying zip fallback: %s", zip_url)
        else:
            logger.warning("Simple URL failed (%s) — trying zip fallback", e)

    # Fallback to zip URL (creates a fresh session to avoid stale cookies)
    if df is None:
        try:
            session_zip = _make_nse_session()
            raw = _download_with_retry(session_zip, zip_url)
            df = _normalise_df(_parse_zip_bytes(raw))
            logger.info("Bhavcopy loaded via zip URL: %d EQ rows", len(df))
        except BhavcopyCopyError as e:
            raise BhavcopyCopyError(
                f"Could not load bhavcopy for {date}: {e}"
            ) from e

    # Universe filter
    if universe_symbols:
        before = len(df)
        df = df[df["SYMBOL"].isin(universe_symbols)]
        logger.info(
            "Universe filter: %d → %d rows (filtered %d symbols not in universe)",
            before, len(df), before - len(df)
        )

    # Convert to list of dicts
    results = []
    for _, row in df.iterrows():
        results.append({
            "symbol":     row["SYMBOL"],
            "prev_high":  round(float(row["HIGH_PRICE"]), 2),
            "prev_low":   round(float(row["LOW_PRICE"]), 2),
            "prev_close": round(float(row["CLOSE_PRICE"]), 2),
        })

    logger.info(
        "fetch_bhavcopy(%s): returning %d records", date.isoformat(), len(results)
    )
    return results
