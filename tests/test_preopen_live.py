"""
tests/test_preopen_live.py

Milestone 5 — Unit tests for scripts/load_preopen_live.py.

All network calls and database writes are mocked so these tests run
fully offline and fast.

Tests cover:
1.  _parse_record(): correctly parses a standard NSE pre-open record.
2.  _parse_record(): handles the nested "metadata" wrapper correctly.
3.  _parse_record(): returns None for a record with no symbol.
4.  _parse_record(): calculates gap_pct correctly.
5.  _write_snapshots(): saves records to DB for symbols in the universe.
6.  _write_snapshots(): skips records not in the known_symbols set.
7.  run_single_poll(): handles empty API response (no records) gracefully.
8.  DATA_MODE=live causes load_daily_levels.py to default to bhavcopy mode.
9.  DATA_MODE=simulated causes load_daily_levels.py to default to preopen mode.

Run with:
    pytest tests/test_preopen_live.py -v
"""

import datetime
import os
import unittest.mock as mock

import pytest

# Import from load_preopen_live script (adjust path if needed)
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.load_preopen_live import (
    _parse_record,
    _write_snapshots,
    run_single_poll,
)


# ── Shared fixtures ──────────────────────────────────────────────────────────

SNAPSHOT_TIME = datetime.datetime(2026, 6, 26, 9, 5, 0)

SAMPLE_RECORDS = [
    {
        "metadata": {
            "symbol":             "RELIANCE",
            "iep":                2960.00,
            "previousClose":      2900.00,
            "totalTradedVolume":  320000,
            "totalBuyQuantity":   180000,
            "totalSellQuantity":  140000,
        }
    },
    {
        "metadata": {
            "symbol":             "TCS",
            "iep":                3900.00,
            "previousClose":      3850.00,
            "totalTradedVolume":  145000,
            "totalBuyQuantity":   90000,
            "totalSellQuantity":  55000,
        }
    },
    {
        "metadata": {
            "symbol":             "INFY",
            "iep":                1580.00,
            "previousClose":      1610.00,
            "totalTradedVolume":  210000,
            "totalBuyQuantity":   95000,
            "totalSellQuantity":  115000,
        }
    },
]

# Flat record (no "metadata" wrapper — older NSE API version)
FLAT_RECORD = {
    "symbol":             "SBIN",
    "iep":                850.00,
    "previousClose":      830.00,
    "totalTradedVolume":  520000,
    "totalBuyQuantity":   310000,
    "totalSellQuantity":  210000,
}


# ── Tests: _parse_record ──────────────────────────────────────────────────────

class TestParseRecord:
    """Tests 1–4: _parse_record parses pre-open API records correctly."""

    def test_standard_nested_record(self):
        """Test 1: standard 'metadata' wrapper record parses correctly."""
        result = _parse_record(SAMPLE_RECORDS[0])
        assert result is not None
        assert result["symbol"] == "RELIANCE"
        assert result["indicative_price"] == pytest.approx(2960.00)
        assert result["prev_close"] == pytest.approx(2900.00)
        assert result["buy_qty"] == 180000
        assert result["sell_qty"] == 140000
        assert result["indicative_volume"] == 320000

    def test_flat_record_no_metadata(self):
        """Test 2: flat record (no 'metadata' key) also parses correctly."""
        result = _parse_record(FLAT_RECORD)
        assert result is not None
        assert result["symbol"] == "SBIN"
        assert result["indicative_price"] == pytest.approx(850.00)

    def test_missing_symbol_returns_none(self):
        """Test 3: record with no symbol returns None."""
        bad_record = {"metadata": {"iep": 100.0, "previousClose": 95.0}}
        result = _parse_record(bad_record)
        assert result is None

    def test_gap_pct_calculation(self):
        """Test 4: gap_pct is calculated correctly."""
        result = _parse_record(SAMPLE_RECORDS[0])
        # (2960 - 2900) / 2900 * 100 ≈ 2.0690
        assert result is not None
        assert result["gap_pct"] == pytest.approx(2.069, abs=0.01)

    def test_gap_pct_negative(self):
        """Test 4b: negative gap (price below prev close)."""
        result = _parse_record(SAMPLE_RECORDS[2])  # INFY: 1580 vs 1610
        assert result is not None
        assert result["gap_pct"] < 0


# ── Tests: _write_snapshots ───────────────────────────────────────────────────

class TestWriteSnapshots:
    """Tests 5–6: _write_snapshots writes/skips records correctly."""

    def _make_db(self):
        """Return a minimal mock DB session."""
        db = mock.MagicMock()
        db.add = mock.MagicMock()
        db.commit = mock.MagicMock()
        return db

    def test_saves_universe_symbols(self):
        """Test 5: only symbols in known_symbols are saved."""
        db = self._make_db()
        known = {"RELIANCE", "TCS"}
        counts = _write_snapshots(SAMPLE_RECORDS, known, SNAPSHOT_TIME, db)
        assert counts["saved"] == 2
        assert db.add.call_count == 2
        db.commit.assert_called_once()

    def test_skips_out_of_universe(self):
        """Test 6: symbols not in known_symbols are skipped."""
        db = self._make_db()
        known = {"RELIANCE"}
        counts = _write_snapshots(SAMPLE_RECORDS, known, SNAPSHOT_TIME, db)
        assert counts["saved"] == 1
        assert counts["skipped"] == 2  # TCS and INFY skipped

    def test_saves_all_when_universe_empty(self):
        """Empty known_symbols set → save all parseable records."""
        db = self._make_db()
        counts = _write_snapshots(SAMPLE_RECORDS, set(), SNAPSHOT_TIME, db)
        assert counts["saved"] == 3

    def test_skips_unparseable_records(self):
        """Records that fail parsing are counted in skipped."""
        db = self._make_db()
        bad = [{"metadata": {}}]  # no symbol
        counts = _write_snapshots(bad, set(), SNAPSHOT_TIME, db)
        assert counts["saved"] == 0
        assert counts["skipped"] == 1


# ── Tests: run_single_poll ───────────────────────────────────────────────────

class TestRunSinglePoll:
    """Test 7: run_single_poll handles empty API response gracefully."""

    def test_empty_api_response(self):
        """Test 7: if NSE returns no records, result contains 'error' key."""
        db = mock.MagicMock()
        db.add = mock.MagicMock()
        db.commit = mock.MagicMock()

        with mock.patch(
            "scripts.load_preopen_live._fetch_preopen_raw", return_value=[]
        ), mock.patch(
            "scripts.load_preopen_live._make_nse_session"
        ), mock.patch(
            "scripts.load_preopen_live.get_universe_symbols", return_value=["RELIANCE", "TCS"]
        ):
            result = run_single_poll(
                universe="nifty_500",
                snapshot_time=SNAPSHOT_TIME,
                db=db,
            )

        assert result.get("error") is not None
        assert result["saved"] == 0


# ── Tests: DATA_MODE env var ─────────────────────────────────────────────────

class TestDataModeEnvVar:
    """Tests 8–9: DATA_MODE env var controls default mode in load_daily_levels."""

    def test_data_mode_live_defaults_to_bhavcopy(self, monkeypatch):
        """Test 8: DATA_MODE=live → default mode is 'bhavcopy'."""
        monkeypatch.setenv("DATA_MODE", "live")

        # Re-import parse_args to pick up the new env var
        import importlib
        import scripts.load_daily_levels as ldl
        importlib.reload(ldl)

        with mock.patch("sys.argv", ["load_daily_levels.py", "--date", "2026-06-25"]):
            args = ldl.parse_args()
        assert args.mode == "bhavcopy"

    def test_data_mode_simulated_defaults_to_preopen(self, monkeypatch):
        """Test 9: DATA_MODE=simulated → default mode is 'preopen'."""
        monkeypatch.setenv("DATA_MODE", "simulated")

        import importlib
        import scripts.load_daily_levels as ldl
        importlib.reload(ldl)

        with mock.patch("sys.argv", ["load_daily_levels.py", "--date", "2026-06-25"]):
            args = ldl.parse_args()
        assert args.mode == "preopen"

    def test_explicit_mode_flag_overrides_env(self, monkeypatch):
        """Test 8b: explicit --mode seed overrides DATA_MODE=live."""
        monkeypatch.setenv("DATA_MODE", "live")

        import importlib
        import scripts.load_daily_levels as ldl
        importlib.reload(ldl)

        with mock.patch("sys.argv", ["load_daily_levels.py", "--date", "2026-06-25", "--mode", "seed"]):
            args = ldl.parse_args()
        assert args.mode == "seed"
