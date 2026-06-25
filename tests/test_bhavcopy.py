"""
tests/test_bhavcopy.py

Milestone 5 — Unit tests for the NSE bhavcopy downloader/parser.

All network calls are mocked so these tests run fully offline and fast.

Tests cover:
1.  _parse_csv_bytes(): parses a minimal CSV string correctly.
2.  _normalise_df(): keeps only EQ rows.
3.  _normalise_df(): drops rows with missing prices.
4.  _normalise_df(): raises BhavcopyCopyError if required columns are missing.
5.  fetch_bhavcopy(): returns the right symbols when called with a universe filter.
6.  fetch_bhavcopy(): excludes non-EQ series rows.
7.  fetch_bhavcopy(): handles a 404 on both URLs gracefully (raises BhavcopyCopyError).
8.  fetch_bhavcopy(): falls back to zip URL if simple URL fails with non-404.
9.  fetch_bhavcopy(): an empty universe_symbols set returns all EQ rows.
10. _normalise_df(): strips whitespace from SYMBOL and SERIES columns.

Run with:
    pytest tests/test_bhavcopy.py -v
"""

import datetime
import io
import textwrap
import unittest.mock as mock
import zipfile

import pytest
import pandas as pd
import requests

from app.collectors.nse_bhavcopy import (
    BhavcopyCopyError,
    _parse_csv_bytes,
    _parse_zip_bytes,
    _normalise_df,
    fetch_bhavcopy,
)


# ── Shared fixtures ──────────────────────────────────────────────────────────

SAMPLE_DATE = datetime.date(2026, 6, 24)

# Minimal CSV matching the real NSE format
SAMPLE_CSV = textwrap.dedent("""\
    SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
    RELIANCE, EQ, 24-Jun-2026, 2890.00, 2900.00, 2950.50, 2880.00, 2940.00, 2940.00, 2915.00, 1000000, 50000.00, 20000, 600000, 60.00
    TCS, EQ, 24-Jun-2026, 3850.00, 3860.00, 3900.00, 3820.00, 3880.00, 3880.00, 3860.00, 500000, 19300.00, 15000, 300000, 60.00
    INFY, BE, 24-Jun-2026, 1600.00, 1595.00, 1650.00, 1590.00, 1620.00, 1620.00, 1618.00, 200000, 3236.00, 8000, 120000, 60.00
    BADSTOCK, EQ, 24-Jun-2026, 100.00, 101.00, , 98.00, 100.00, 100.00, 100.00, 5000, 50.00, 200, 3000, 60.00
""").encode("utf-8")

# Matching zip format CSV (new-format column names)
SAMPLE_ZIP_CSV = textwrap.dedent("""\
    TckrSymb,SctySrs,XpryDt,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Xpltry1,Xpltry2,Xpltry3
    HDFCBANK,EQ,,,1700.00,1740.00,1690.00,1725.00,1720.00,1700.00,,,,350000,60375000.00,22000,,,,,,
    WIPRO,EQ,,,530.00,545.00,525.00,535.00,533.00,530.00,,,,175000,93575000.00,9000,,,,,,
""").encode("utf-8")


def _make_zip_bytes(csv_bytes: bytes) -> bytes:
    """Create a zip archive containing a single CSV file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BhavCopy_NSE_CM_0_0_0_20260624_F_0000.csv", csv_bytes.decode("utf-8"))
    return buf.getvalue()


# ── Helper: mock a successful requests.get ───────────────────────────────────

def _mock_get(status_code: int, content: bytes):
    """Return a mock Response object."""
    resp = mock.MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.raise_for_status = mock.MagicMock()
    return resp


# ── Tests ────────────────────────────────────────────────────────────────────

class TestParseCsvBytes:
    """Test 1: _parse_csv_bytes returns a DataFrame with stripped column names."""

    def test_columns_stripped(self):
        df = _parse_csv_bytes(SAMPLE_CSV)
        assert "SYMBOL" in df.columns
        assert "HIGH_PRICE" in df.columns
        assert "SERIES" in df.columns

    def test_row_count(self):
        df = _parse_csv_bytes(SAMPLE_CSV)
        # 4 data rows (including the bad stock and BE row)
        assert len(df) == 4


class TestNormaliseDF:
    """Tests 2, 3, 4, 10: _normalise_df filtering and validation."""

    def test_keeps_only_eq(self):
        """Test 2: only EQ rows survive normalisation."""
        df_raw = _parse_csv_bytes(SAMPLE_CSV)
        df = _normalise_df(df_raw)
        assert all(df["SYMBOL"].isin(["RELIANCE", "TCS"]))
        # INFY is BE series — excluded
        assert "INFY" not in df["SYMBOL"].values

    def test_drops_missing_prices(self):
        """Test 3: row with blank HIGH_PRICE is dropped."""
        df_raw = _parse_csv_bytes(SAMPLE_CSV)
        df = _normalise_df(df_raw)
        # BADSTOCK has blank HIGH_PRICE — should be gone
        assert "BADSTOCK" not in df["SYMBOL"].values

    def test_raises_on_missing_columns(self):
        """Test 4: missing required columns raise BhavcopyCopyError."""
        bad_csv = b"SYMBOL,DATE\nRELIANCE,2026-06-24\n"
        df_raw = _parse_csv_bytes(bad_csv)
        with pytest.raises(BhavcopyCopyError, match="missing expected columns"):
            _normalise_df(df_raw)

    def test_strips_whitespace_in_symbol(self):
        """Test 10: symbols with leading/trailing spaces are cleaned."""
        padded_csv = textwrap.dedent("""\
            SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
             RELIANCE , EQ, 24-Jun-2026, 2890.00, 2900.00, 2950.50, 2880.00, 2940.00, 2940.00, 2915.00, 1000000, 50000.00, 20000, 600000, 60.00
        """).encode("utf-8")
        df_raw = _parse_csv_bytes(padded_csv)
        df = _normalise_df(df_raw)
        assert "RELIANCE" in df["SYMBOL"].values


class TestFetchBhavcopy:
    """Tests 5–9: fetch_bhavcopy() integration with mocked HTTP."""

    def _patch_session(self, simple_response, zip_response=None):
        """
        Return a context manager that patches _make_nse_session directly.
        Each call to _make_nse_session() returns a fresh mock session.
        simple_response: the Response for the plain CSV URL.
        zip_response: the Response for the zip URL (only used if simple fails).
        """
        # Session 1: used for the simple URL attempt
        session1 = mock.MagicMock()
        session1.get.side_effect = [simple_response]

        if zip_response is not None:
            # Session 2: used for the zip fallback
            session2 = mock.MagicMock()
            session2.get.side_effect = [zip_response]
            sessions = [session1, session2]
        else:
            sessions = [session1]

        return mock.patch(
            "app.collectors.nse_bhavcopy._make_nse_session",
            side_effect=sessions,
        )

    def test_returns_filtered_symbols(self):
        """Test 5: universe filter returns only matching symbols."""
        with self._patch_session(_mock_get(200, SAMPLE_CSV)):
            results = fetch_bhavcopy(SAMPLE_DATE, universe_symbols={"RELIANCE"})
        symbols = [r["symbol"] for r in results]
        assert symbols == ["RELIANCE"]

    def test_excludes_non_eq(self):
        """Test 6: INFY (BE series) does not appear in results."""
        with self._patch_session(_mock_get(200, SAMPLE_CSV)):
            results = fetch_bhavcopy(SAMPLE_DATE)
        symbols = [r["symbol"] for r in results]
        assert "INFY" not in symbols

    def test_404_raises_error(self):
        """Test 7: 404 on simple URL propagates immediately as BhavcopyCopyError."""
        not_found_404 = mock.MagicMock()
        not_found_404.status_code = 404
        not_found_404.content = b""

        # Session 1 (simple URL) returns 404 immediately
        session1 = mock.MagicMock()
        session1.get.side_effect = [not_found_404]

        # Session 2 (zip fallback) also returns 404
        session2 = mock.MagicMock()
        session2.get.side_effect = [not_found_404]

        with mock.patch(
            "app.collectors.nse_bhavcopy._make_nse_session",
            side_effect=[session1, session2],
        ):
            with pytest.raises(BhavcopyCopyError):
                fetch_bhavcopy(SAMPLE_DATE)

    def test_falls_back_to_zip(self):
        """Test 8: if simple URL fails (500 x3 retries), fall back to zip URL."""
        server_error = mock.MagicMock()
        server_error.status_code = 500
        server_error.content = b""

        zip_bytes = _make_zip_bytes(SAMPLE_ZIP_CSV)
        zip_resp = _mock_get(200, zip_bytes)

        # Session 1: all 3 retry attempts return 500
        session1 = mock.MagicMock()
        session1.get.side_effect = [server_error, server_error, server_error]

        # Session 2: zip URL returns 200 with valid zip content
        session2 = mock.MagicMock()
        session2.get.side_effect = [zip_resp]

        with mock.patch(
            "app.collectors.nse_bhavcopy._make_nse_session",
            side_effect=[session1, session2],
        ):
            results = fetch_bhavcopy(SAMPLE_DATE)

        symbols = [r["symbol"] for r in results]
        assert "HDFCBANK" in symbols
        assert "WIPRO" in symbols

    def test_empty_universe_returns_all(self):
        """Test 9: passing an empty set returns all EQ rows."""
        with self._patch_session(_mock_get(200, SAMPLE_CSV)):
            results = fetch_bhavcopy(SAMPLE_DATE, universe_symbols=set())
        # Should include RELIANCE and TCS (EQ); not INFY (BE) or BADSTOCK (missing price)
        symbols = [r["symbol"] for r in results]
        assert "RELIANCE" in symbols
        assert "TCS" in symbols
        assert "INFY" not in symbols

    def test_correct_ohlc_values(self):
        """Bonus: parsed OHLC values match the CSV data."""
        with self._patch_session(_mock_get(200, SAMPLE_CSV)):
            results = fetch_bhavcopy(SAMPLE_DATE, universe_symbols={"RELIANCE"})
        assert len(results) == 1
        r = results[0]
        assert r["prev_high"] == pytest.approx(2950.50)
        assert r["prev_low"]  == pytest.approx(2880.00)
        assert r["prev_close"] == pytest.approx(2940.00)
