"""
tests/test_event_classifier.py

Unit tests for the AI event classifier (app/ai/event_classifier.py).

All tests run offline — the LLM is mocked via monkeypatching so no API key
is required. Tests verify:

1.  Empty raw_text → returns NO_EVENT without calling LLM.
2.  Whitespace-only raw_text → returns NO_EVENT.
3.  Missing API key → mock path is used, returns valid structure.
4.  All returned event_type values are in the approved taxonomy.
5.  All returned sentiment values are in the approved set.
6.  Confidence is always a float in [0.0, 1.0].
7.  Label is always a non-empty string.
8.  A well-formed mock LLM response is parsed correctly.
9.  A malformed JSON response does not crash — falls back to GENERAL_NEWS.
10. An unknown event_type in the response is corrected to GENERAL_NEWS.
11. An unknown sentiment is corrected to NEUTRAL.
12. Confidence > 1.0 is clamped to 1.0.
13. Confidence < 0.0 is clamped to 0.0.
14. A realistic earnings headline classifies correctly via mock path.
15. A realistic risk headline classifies correctly via mock path.

Run with:
    pytest tests/test_event_classifier.py -v
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from app.ai.event_classifier import (
    classify_event,
    _validate_and_fix,
    _mock_result,
    VALID_EVENT_TYPES,
    VALID_SENTIMENTS,
)


# ── Helper: build a fake OpenAI response ─────────────────────────────────────

def _fake_openai_response(event_type: str, sentiment: str, confidence: float, label: str):
    """Create a mock OpenAI response object that returns the given JSON."""
    content = json.dumps({
        "event_type": event_type,
        "sentiment": sentiment,
        "confidence": confidence,
        "label": label,
    })
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


# ── Tests: empty / whitespace input ──────────────────────────────────────────

def test_empty_raw_text_returns_no_event():
    """Empty raw_text must return NO_EVENT without touching the LLM."""
    result = classify_event(symbol="RELIANCE", trade_date="2026-06-25", raw_text="")
    assert result["event_type"] == "NO_EVENT"
    assert result["confidence"] == 1.0


def test_whitespace_raw_text_returns_no_event():
    """Whitespace-only raw_text must also return NO_EVENT."""
    result = classify_event(symbol="INFY", trade_date="2026-06-25", raw_text="   \n\t  ")
    assert result["event_type"] == "NO_EVENT"


# ── Tests: mock path (no API key) ─────────────────────────────────────────────

def test_missing_api_key_uses_mock(monkeypatch):
    """When LLM_API_KEY is blank, classifier uses the mock path."""
    monkeypatch.setattr("app.ai.event_classifier.settings.llm_api_key", "")

    result = classify_event(
        symbol="RELIANCE",
        trade_date="2026-06-25",
        raw_text="Reliance Q4FY26 results: Net profit up 18% YoY",
    )
    assert result["_mock"] is True
    assert result["event_type"] in VALID_EVENT_TYPES


def test_mock_result_structure_is_valid():
    """_mock_result always returns a dict with the four required keys."""
    result = _mock_result("Some headline about earnings results")
    assert "event_type" in result
    assert "sentiment" in result
    assert "confidence" in result
    assert "label" in result
    assert result["event_type"] in VALID_EVENT_TYPES
    assert result["sentiment"] in VALID_SENTIMENTS
    assert 0.0 <= result["confidence"] <= 1.0
    assert isinstance(result["label"], str) and len(result["label"]) > 0


# ── Tests: live LLM path (mocked OpenAI client) ───────────────────────────────

def test_well_formed_llm_response_is_parsed_correctly(monkeypatch):
    """A correct LLM JSON response is parsed into the right fields."""
    monkeypatch.setattr("app.ai.event_classifier.settings.llm_api_key", "fake-key")
    monkeypatch.setattr("app.ai.event_classifier.settings.llm_model", "gpt-4o-mini")

    fake_resp = _fake_openai_response(
        event_type="EARNINGS",
        sentiment="POSITIVE",
        confidence=0.95,
        label="Q4 net profit +18% YoY beat",
    )

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = fake_resp

    with patch("app.ai.event_classifier.OpenAI", return_value=mock_client):
        result = classify_event(
            symbol="RELIANCE",
            trade_date="2026-06-25",
            raw_text="Reliance Q4 net profit up 18% YoY",
        )

    assert result["event_type"] == "EARNINGS"
    assert result["sentiment"] == "POSITIVE"
    assert abs(result["confidence"] - 0.95) < 0.01
    assert "18%" in result["label"] or "profit" in result["label"].lower()


def test_malformed_json_falls_back_to_general_news(monkeypatch):
    """When LLM returns invalid JSON, classifier falls back to GENERAL_NEWS."""
    monkeypatch.setattr("app.ai.event_classifier.settings.llm_api_key", "fake-key")

    msg = MagicMock()
    msg.content = "This is not JSON at all!"
    choice = MagicMock()
    choice.message = msg
    fake_resp = MagicMock()
    fake_resp.choices = [choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = fake_resp

    with patch("app.ai.event_classifier.OpenAI", return_value=mock_client):
        result = classify_event(
            symbol="INFY",
            trade_date="2026-06-25",
            raw_text="Some news headline",
        )

    assert result["event_type"] == "GENERAL_NEWS"
    assert result["sentiment"] == "NEUTRAL"
    assert 0.0 <= result["confidence"] <= 1.0


def test_llm_exception_falls_back_gracefully(monkeypatch):
    """When the OpenAI call throws, classifier returns GENERAL_NEWS without crashing."""
    monkeypatch.setattr("app.ai.event_classifier.settings.llm_api_key", "fake-key")

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("Network timeout")

    with patch("app.ai.event_classifier.OpenAI", return_value=mock_client):
        result = classify_event(
            symbol="TCS",
            trade_date="2026-06-25",
            raw_text="TCS board meeting announcement",
        )

    assert result["event_type"] == "GENERAL_NEWS"


# ── Tests: _validate_and_fix ──────────────────────────────────────────────────

def test_unknown_event_type_corrected_to_general_news():
    """An unknown event_type in the LLM response is replaced with GENERAL_NEWS."""
    raw = {"event_type": "UNKNOWN_TYPE", "sentiment": "NEUTRAL", "confidence": 0.8, "label": "test"}
    result = _validate_and_fix(raw, "INFY")
    assert result["event_type"] == "GENERAL_NEWS"


def test_unknown_sentiment_corrected_to_neutral():
    """An unknown sentiment in the LLM response is replaced with NEUTRAL."""
    raw = {"event_type": "EARNINGS", "sentiment": "MIXED", "confidence": 0.7, "label": "test"}
    result = _validate_and_fix(raw, "INFY")
    assert result["sentiment"] == "NEUTRAL"


def test_confidence_above_1_is_clamped():
    """Confidence > 1.0 must be clamped to 1.0."""
    raw = {"event_type": "EARNINGS", "sentiment": "POSITIVE", "confidence": 1.5, "label": "test"}
    result = _validate_and_fix(raw, "INFY")
    assert result["confidence"] <= 1.0


def test_confidence_below_0_is_clamped():
    """Confidence < 0.0 must be clamped to 0.0."""
    raw = {"event_type": "RISK", "sentiment": "NEGATIVE", "confidence": -0.3, "label": "test"}
    result = _validate_and_fix(raw, "INFY")
    assert result["confidence"] >= 0.0


def test_empty_label_gets_fallback():
    """An empty label in the LLM response gets a sensible fallback."""
    raw = {"event_type": "MACRO", "sentiment": "NEUTRAL", "confidence": 0.6, "label": ""}
    result = _validate_and_fix(raw, "INFY")
    assert len(result["label"]) > 0


def test_label_is_truncated_at_300_chars():
    """Labels longer than 300 chars are truncated."""
    long_label = "x" * 500
    raw = {"event_type": "GENERAL_NEWS", "sentiment": "NEUTRAL", "confidence": 0.5, "label": long_label}
    result = _validate_and_fix(raw, "INFY")
    assert len(result["label"]) <= 300


# ── Tests: realistic mock classifications ────────────────────────────────────

@pytest.mark.parametrize("text,expected_type", [
    ("Reliance Q4FY26 results: Net profit up 18% YoY", "EARNINGS"),
    ("Infosys guidance raised to 8-10% for FY27", "GUIDANCE"),
    ("Motilal Oswal initiates coverage with BUY and target price Rs 280", "BROKER_RATING"),
    ("CRISIL upgrades credit rating from AA- to AA", "BROKER_RATING"),
    ("Tata Motors announces dividend of Rs 5 per share", "CORPORATE_ACTION"),
    ("SEBI regulatory action against promoter", "RISK"),
])
def test_mock_classifies_realistic_headlines(text, expected_type, monkeypatch):
    """Mock classifier correctly identifies event type from realistic headlines."""
    monkeypatch.setattr("app.ai.event_classifier.settings.llm_api_key", "")

    result = classify_event(symbol="TEST", trade_date="2026-06-25", raw_text=text)
    assert result["event_type"] == expected_type, (
        f"Expected {expected_type} for: '{text}' but got {result['event_type']}"
    )
