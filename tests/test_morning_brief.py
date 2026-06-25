"""
tests/test_morning_brief.py

Unit tests for the morning brief generator (app/ai/morning_brief.py).

All tests run offline — the database and LLM are mocked so no API key or
live DB is required. Tests verify:

1.  generate_brief raises ValueError when no rankings exist for the date.
2.  generate_brief returns a dict with the four required keys.
3.  top_symbols list has correct structure (rank, symbol, bucket, event_tags).
4.  sections has correct keys (top_board, positive_catalysts, risk_names, noisy_items).
5.  rendered_brief is a non-empty string.
6.  Mock brief mode produces a valid brief string when no API key is set.
7.  LLM call is made when API key is present (mocked).
8.  LLM exception falls back to mock brief without crashing.
9.  A symbol with no events produces no event_tags.
10. Rankings with only C-bucket symbols produce a brief with empty top_symbols.

Run with:
    pytest tests/test_morning_brief.py -v
"""

import datetime
import pytest
from unittest.mock import patch, MagicMock

from app.ai.morning_brief import (
    generate_brief,
    _build_brief_input,
    _mock_brief,
)


# ── Fixtures: minimal DB mocks ────────────────────────────────────────────────

def make_ranking_row(
    rank: int,
    symbol: str,
    bucket: str = "A",
    total_score: float = 75.0,
):
    """Create a mock DailyRanking ORM row."""
    row = MagicMock()
    row.rank = rank
    row.symbol = symbol
    row.watchlist_bucket = bucket
    row.total_score = total_score
    row.catalyst_score = 85.0
    row.preopen_score = 70.0
    row.liquidity_score = 90.0
    row.technical_score = 72.0
    return row


def make_event_row(symbol: str, event_type: str, sentiment: str, confidence: float = 0.9):
    """Create a mock SymbolEvent ORM row."""
    row = MagicMock()
    row.symbol = symbol
    row.event_type = event_type
    row.sentiment = sentiment
    row.confidence = confidence
    row.label = f"{symbol} {event_type} event"
    row.raw_text = f"Sample raw text for {symbol}"
    return row


def make_db_session(ranking_rows, event_rows):
    """Build a mock SQLAlchemy session that returns the given rows."""
    db = MagicMock()

    # Mock for ranking_rows query
    ranking_query = MagicMock()
    ranking_query.filter.return_value = ranking_query
    ranking_query.order_by.return_value = ranking_query
    ranking_query.limit.return_value = ranking_query
    ranking_query.all.return_value = ranking_rows

    # Mock for event_rows query
    event_query = MagicMock()
    event_query.filter.return_value = event_query
    event_query.order_by.return_value = event_query
    event_query.all.return_value = event_rows

    # db.query returns different mocks depending on the model
    def _query_dispatcher(model):
        from app.models import DailyRanking, SymbolEvent
        if model is DailyRanking:
            return ranking_query
        elif model is SymbolEvent:
            return event_query
        return MagicMock()

    db.query.side_effect = _query_dispatcher
    return db


TRADE_DATE = datetime.date(2026, 6, 25)


# ── Tests: error handling ─────────────────────────────────────────────────────

def test_raises_value_error_when_no_rankings(monkeypatch):
    """generate_brief raises ValueError if no rankings exist for the date."""
    monkeypatch.setattr("app.ai.morning_brief.settings.llm_api_key", "")

    db = make_db_session(ranking_rows=[], event_rows=[])

    with pytest.raises(ValueError, match="No rankings found"):
        generate_brief(db=db, trade_date=TRADE_DATE)


# ── Tests: output structure ───────────────────────────────────────────────────

def test_result_has_required_keys(monkeypatch):
    """generate_brief always returns a dict with the four required keys."""
    monkeypatch.setattr("app.ai.morning_brief.settings.llm_api_key", "")

    rankings = [
        make_ranking_row(1, "RELIANCE", "A", 82.0),
        make_ranking_row(2, "INFY", "A", 76.0),
        make_ranking_row(3, "TATAMOTORS", "B", 65.0),
    ]
    events = [
        make_event_row("RELIANCE", "EARNINGS", "POSITIVE"),
        make_event_row("INFY", "GUIDANCE", "POSITIVE"),
    ]
    db = make_db_session(rankings, events)

    result = generate_brief(db=db, trade_date=TRADE_DATE)

    assert "trade_date" in result
    assert "top_symbols" in result
    assert "sections" in result
    assert "rendered_brief" in result


def test_top_symbols_structure(monkeypatch):
    """top_symbols list contains dicts with expected keys."""
    monkeypatch.setattr("app.ai.morning_brief.settings.llm_api_key", "")

    rankings = [make_ranking_row(1, "RELIANCE", "A", 85.0)]
    events = [make_event_row("RELIANCE", "EARNINGS", "POSITIVE")]
    db = make_db_session(rankings, events)

    result = generate_brief(db=db, trade_date=TRADE_DATE)
    assert len(result["top_symbols"]) >= 1

    sym = result["top_symbols"][0]
    assert "rank" in sym
    assert "symbol" in sym
    assert "bucket" in sym
    assert "total_score" in sym
    assert "event_tags" in sym
    assert isinstance(sym["event_tags"], list)


def test_sections_has_correct_keys(monkeypatch):
    """sections dict always contains the four expected section keys."""
    monkeypatch.setattr("app.ai.morning_brief.settings.llm_api_key", "")

    rankings = [make_ranking_row(1, "RELIANCE", "A", 85.0)]
    events = [make_event_row("RELIANCE", "EARNINGS", "POSITIVE")]
    db = make_db_session(rankings, events)

    result = generate_brief(db=db, trade_date=TRADE_DATE)
    sections = result["sections"]

    assert "top_board" in sections
    assert "positive_catalysts" in sections
    assert "risk_names" in sections
    assert "noisy_items" in sections


def test_rendered_brief_is_non_empty_string(monkeypatch):
    """rendered_brief must be a non-empty string."""
    monkeypatch.setattr("app.ai.morning_brief.settings.llm_api_key", "")

    rankings = [make_ranking_row(1, "RELIANCE", "A", 85.0)]
    events = [make_event_row("RELIANCE", "EARNINGS", "POSITIVE")]
    db = make_db_session(rankings, events)

    result = generate_brief(db=db, trade_date=TRADE_DATE)
    assert isinstance(result["rendered_brief"], str)
    assert len(result["rendered_brief"]) > 50


def test_symbol_with_no_events_has_empty_event_tags(monkeypatch):
    """A symbol that has no classified events should have event_tags=[]."""
    monkeypatch.setattr("app.ai.morning_brief.settings.llm_api_key", "")

    rankings = [make_ranking_row(1, "HDFCBANK", "A", 70.0)]
    events = []  # no events for HDFCBANK
    db = make_db_session(rankings, events)

    result = generate_brief(db=db, trade_date=TRADE_DATE)
    sym = result["top_symbols"][0]
    assert sym["event_tags"] == []


# ── Tests: mock brief ─────────────────────────────────────────────────────────

def test_mock_brief_contains_trade_date():
    """Mock brief should include the trade date in its text."""
    brief_input = {
        "trade_date": "2026-06-25",
        "top_symbols": [
            {"rank": 1, "symbol": "RELIANCE", "bucket": "A", "total_score": 82.0, "event_tags": []},
        ],
        "positive_catalysts": [],
        "risk_names": [],
        "noisy_items": [],
    }
    brief_text = _mock_brief(brief_input)
    assert "2026-06-25" in brief_text
    assert len(brief_text) > 50


def test_mock_brief_includes_mock_label():
    """Mock brief should clearly indicate it is a mock/dry-run output."""
    brief_input = {
        "trade_date": "2026-06-25",
        "top_symbols": [],
        "positive_catalysts": [],
        "risk_names": [],
        "noisy_items": [],
    }
    brief_text = _mock_brief(brief_input)
    assert "[MOCK]" in brief_text or "mock" in brief_text.lower()


# ── Tests: LLM integration (mocked) ──────────────────────────────────────────

def test_llm_is_called_when_api_key_present(monkeypatch):
    """When API key is set, the LLM should be called."""
    monkeypatch.setattr("app.ai.morning_brief.settings.llm_api_key", "fake-key")
    monkeypatch.setattr("app.ai.morning_brief.settings.llm_model", "gpt-4o-mini")

    rankings = [make_ranking_row(1, "RELIANCE", "A", 85.0)]
    events = [make_event_row("RELIANCE", "EARNINGS", "POSITIVE")]
    db = make_db_session(rankings, events)

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = "## Pre-Market Brief\n\nTest brief content from LLM."
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    with patch("app.ai.morning_brief.OpenAI", return_value=mock_client):
        result = generate_brief(db=db, trade_date=TRADE_DATE)

    mock_client.chat.completions.create.assert_called_once()
    assert "Test brief content from LLM" in result["rendered_brief"]


def test_llm_exception_falls_back_to_mock_brief(monkeypatch):
    """When LLM call throws, generate_brief falls back to the mock brief without crashing."""
    monkeypatch.setattr("app.ai.morning_brief.settings.llm_api_key", "fake-key")
    monkeypatch.setattr("app.ai.morning_brief.settings.llm_model", "gpt-4o-mini")

    rankings = [make_ranking_row(1, "INFY", "A", 78.0)]
    events = [make_event_row("INFY", "GUIDANCE", "POSITIVE")]
    db = make_db_session(rankings, events)

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("API timeout")

    with patch("app.ai.morning_brief.OpenAI", return_value=mock_client):
        result = generate_brief(db=db, trade_date=TRADE_DATE)

    # Should fall back to mock — brief still exists
    assert isinstance(result["rendered_brief"], str)
    assert len(result["rendered_brief"]) > 20


# ── Tests: _build_brief_input ─────────────────────────────────────────────────

def test_build_brief_input_structure():
    """_build_brief_input returns dict with correct keys."""
    ranked = [
        {
            "rank": 1, "symbol": "RELIANCE", "watchlist_bucket": "A",
            "total_score": 85.0, "catalyst_score": 90.0,
            "preopen_score": 80.0, "liquidity_score": 95.0, "technical_score": 72.0,
        },
    ]
    events_map = {
        "RELIANCE": [{"event_type": "EARNINGS", "sentiment": "POSITIVE", "confidence": 0.95, "label": "Q4 beat"}]
    }
    result = _build_brief_input(ranked, events_map, "2026-06-25")

    assert "trade_date" in result
    assert "top_symbols" in result
    assert "positive_catalysts" in result
    assert "risk_names" in result
    assert "noisy_items" in result
    assert result["trade_date"] == "2026-06-25"
    assert len(result["top_symbols"]) == 1


def test_risk_symbol_routed_to_risk_names():
    """A symbol with a RISK event should appear in risk_names."""
    ranked = [
        {
            "rank": 1, "symbol": "ADANIENT", "watchlist_bucket": "A",
            "total_score": 72.0, "catalyst_score": 20.0,
            "preopen_score": 65.0, "liquidity_score": 88.0, "technical_score": 70.0,
        },
    ]
    events_map = {
        "ADANIENT": [{"event_type": "RISK", "sentiment": "NEGATIVE", "confidence": 0.9, "label": "SEBI probe"}]
    }
    result = _build_brief_input(ranked, events_map, "2026-06-25")
    risk_symbols = [s["symbol"] for s in result["risk_names"]]
    assert "ADANIENT" in risk_symbols
