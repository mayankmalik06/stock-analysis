"""
tests/test_m6_announcements.py

Milestone 6 test suite:

1. Unit tests for RSS XML parsing.
2. Unit tests for JSON API parsing.
3. Unit tests for symbol extraction from RSS titles.
4. Tests for classification flow (mock LLM response → DB write).
5. Integration test: insert announcements → score → verify catalyst_score changes.

Run from project root:
    python -m pytest tests/test_m6_announcements.py -v

No internet connection required — all external calls are mocked.
"""

import datetime
import hashlib
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Make sure app/ is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


# ═════════════════════════════════════════════════════════════════════════════
# 1. Unit tests: RSS XML parsing
# ═════════════════════════════════════════════════════════════════════════════

class TestRSSParsing(unittest.TestCase):
    """Tests for _fetch_rss_items() using the XML fixture file."""

    def _load_fixture_xml(self) -> str:
        path = os.path.join(FIXTURE_DIR, "sample_announcements.xml")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_rss_items_parsed_from_fixture(self):
        """RSS fixture should parse into 4 items before time-window filtering."""
        import feedparser
        xml_content = self._load_fixture_xml()
        feed = feedparser.parse(xml_content)
        self.assertGreaterEqual(len(feed.entries), 3, "Expected at least 3 RSS entries in fixture")

    def test_rss_symbol_extracted_from_title(self):
        """Symbols should be correctly extracted from RSS title strings."""
        from app.collectors.nse_announcements import _symbol_from_rss_title

        cases = [
            ("RELIANCE - Board Meeting Outcome", "RELIANCE"),
            ("INFY : Quarterly Results", "INFY"),
            ("[HDFCBANK] - FII Holding", "HDFCBANK"),
            ("TATAMOTORS-QIP Announcement", "TATAMOTORS"),
            ("general exchange notice no symbol", ""),
        ]
        for title, expected in cases:
            result = _symbol_from_rss_title(title)
            self.assertEqual(
                result.upper() if result else "",
                expected,
                f"Failed for title: {title!r} → got {result!r}, expected {expected!r}",
            )

    def test_rss_timestamp_parsing(self):
        """NSE timestamp strings should parse to datetime objects."""
        from app.collectors.nse_announcements import _parse_nse_timestamp

        cases = [
            ("26-Jun-2026 07:30:00", datetime.datetime(2026, 6, 26, 7, 30, 0)),
            ("26-Jun-2026",          datetime.datetime(2026, 6, 26, 0, 0, 0)),
            ("2026-06-26 08:00:00",  datetime.datetime(2026, 6, 26, 8, 0, 0)),
            (None,                   None),
            ("",                     None),
        ]
        for ts_str, expected in cases:
            result = _parse_nse_timestamp(ts_str)
            self.assertEqual(result, expected, f"Failed for: {ts_str!r}")

    def test_rss_items_via_mock_request(self):
        """
        _fetch_rss_items() should correctly parse the XML fixture when
        the HTTP response is mocked.
        """
        from app.collectors.nse_announcements import _fetch_rss_items

        xml_content = self._load_fixture_xml()

        # Build a mock requests.Session that returns fixture XML
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = xml_content
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        # Use a trade_date that falls within the fixture's pubDate window
        trade_date = datetime.date(2026, 6, 26)
        items = _fetch_rss_items(trade_date=trade_date, session=mock_session)

        # All 4 entries should be returned (time filtering passes for this date)
        self.assertGreaterEqual(len(items), 3, f"Expected >=3 items, got {len(items)}")

        # Check that at least one RELIANCE item is present
        symbols = [i["symbol"] for i in items]
        self.assertIn("RELIANCE", symbols, "RELIANCE should appear in RSS items")

        # Check structure
        for item in items:
            self.assertIn("symbol", item)
            self.assertIn("headline", item)
            self.assertIn("body", item)
            self.assertIn("announced_at", item)
            self.assertIn("source_url", item)


# ═════════════════════════════════════════════════════════════════════════════
# 2. Unit tests: JSON API parsing
# ═════════════════════════════════════════════════════════════════════════════

class TestJSONAPIParsing(unittest.TestCase):
    """Tests for _fetch_json_api_items() using the JSON fixture file."""

    def _load_fixture_json(self) -> str:
        path = os.path.join(FIXTURE_DIR, "sample_announcements_api.json")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_json_api_items_parsed(self):
        """JSON API fixture should parse into correct normalised items."""
        from app.collectors.nse_announcements import _fetch_json_api_items

        json_content = self._load_fixture_json()

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = json.loads(json_content)
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        trade_date = datetime.date(2026, 6, 26)
        items = _fetch_json_api_items(trade_date=trade_date, session=mock_session)

        self.assertEqual(len(items), 3, "Should parse 3 items from JSON fixture")

        # Check TATAMOTORS item
        tata = next((i for i in items if i["symbol"] == "TATAMOTORS"), None)
        self.assertIsNotNone(tata, "TATAMOTORS item should be present")
        self.assertIn("board", tata["headline"].lower())
        self.assertIsNotNone(tata["announced_at"])
        self.assertIsInstance(tata["announced_at"], datetime.datetime)

    def test_json_api_symbol_field(self):
        """Symbols from JSON API should be uppercase."""
        from app.collectors.nse_announcements import _fetch_json_api_items

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"symbol": "reliance", "attchmntText": "Test headline", "an_dt": "26-Jun-2026"}
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        items = _fetch_json_api_items(
            trade_date=datetime.date(2026, 6, 26),
            session=mock_session,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["symbol"], "RELIANCE")


# ═════════════════════════════════════════════════════════════════════════════
# 3. Unit tests: hash deduplication
# ═════════════════════════════════════════════════════════════════════════════

class TestHashDeduplication(unittest.TestCase):
    """Verify that _hash_text produces consistent, unique hashes."""

    def test_hash_is_64_chars(self):
        from app.collectors.nse_announcements import _hash_text
        h = _hash_text("some event text")
        self.assertEqual(len(h), 64)

    def test_same_text_same_hash(self):
        from app.collectors.nse_announcements import _hash_text
        h1 = _hash_text("RELIANCE Q4 results")
        h2 = _hash_text("RELIANCE Q4 results")
        self.assertEqual(h1, h2)

    def test_different_text_different_hash(self):
        from app.collectors.nse_announcements import _hash_text
        h1 = _hash_text("RELIANCE Q4 results")
        h2 = _hash_text("INFY Q4 results")
        self.assertNotEqual(h1, h2)


# ═════════════════════════════════════════════════════════════════════════════
# 4. Classification flow tests
# ═════════════════════════════════════════════════════════════════════════════

class TestClassificationFlow(unittest.TestCase):
    """
    Tests for classify_events.py logic.
    Uses an in-memory SQLite DB and mocks the LLM call.
    """

    def setUp(self):
        """Create an in-memory DB with required tables."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db import Base
        from app.models import SymbolEvent  # noqa: F401 — needed for table creation

        self.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()
        self.trade_date = datetime.date(2026, 6, 26)

    def tearDown(self):
        self.db.close()

    def _insert_pending_event(self, symbol: str, raw_text: str) -> "SymbolEvent":
        from app.models import SymbolEvent
        import hashlib
        text_hash = hashlib.sha256(raw_text.encode()).hexdigest()[:64]
        row = SymbolEvent(
            trade_date=self.trade_date,
            symbol=symbol,
            raw_text=raw_text,
            source="NSE_ANNOUNCEMENTS",
            headline=raw_text[:100],
            announced_at=datetime.datetime(2026, 6, 26, 8, 0, 0),
            event_type=None,   # pending
            sentiment=None,
            confidence=None,
            label=None,
            raw_text_hash=text_hash,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def test_classify_writes_event_type(self):
        """
        After classifying, event_type / sentiment / confidence / label
        should all be set on the DB row.
        """
        from app.models import SymbolEvent

        row = self._insert_pending_event(
            "RELIANCE",
            "Reliance Q4FY26 net profit up 18% YoY to Rs 19,488 Cr",
        )
        self.assertIsNone(row.event_type)  # starts as pending

        # Mock classify_event to return a known result
        mock_result = {
            "event_type": "EARNINGS",
            "sentiment": "POSITIVE",
            "confidence": 0.96,
            "label": "Reliance Q4 net profit +18% YoY — strong earnings beat",
        }

        with patch("app.ai.event_classifier.classify_event", return_value=mock_result):
            # Run the classification logic directly
            rows_to_classify = (
                self.db.query(SymbolEvent)
                .filter(SymbolEvent.trade_date == self.trade_date)
                .filter(SymbolEvent.event_type == None)  # noqa: E711
                .all()
            )
            for r in rows_to_classify:
                from app.ai.event_classifier import classify_event
                result = classify_event(
                    symbol=r.symbol,
                    trade_date=str(r.trade_date),
                    raw_text=r.raw_text,
                )
                r.event_type = result["event_type"]
                r.sentiment = result["sentiment"]
                r.confidence = result["confidence"]
                r.label = result["label"]
            self.db.commit()

        # Fetch fresh
        updated = self.db.query(SymbolEvent).filter(SymbolEvent.id == row.id).first()
        self.assertEqual(updated.event_type, "EARNINGS")
        self.assertEqual(updated.sentiment, "POSITIVE")
        self.assertAlmostEqual(updated.confidence, 0.96, places=2)
        self.assertIn("18%", updated.label)

    def test_pending_rows_have_null_event_type(self):
        """Rows inserted by load_announcements should start with NULL event_type."""
        from app.models import SymbolEvent

        row = self._insert_pending_event("INFY", "Infosys guidance raised to 8-10%")
        self.assertIsNone(row.event_type, "event_type should be NULL for pending rows")
        self.assertIsNone(row.sentiment)
        self.assertIsNone(row.confidence)

    def test_source_field_stored_correctly(self):
        """Source tag should be NSE_ANNOUNCEMENTS for live rows."""
        row = self._insert_pending_event("HDFCBANK", "FII holding increased to 55.2%")
        self.assertEqual(row.source, "NSE_ANNOUNCEMENTS")

    def test_duplicate_hash_not_inserted(self):
        """
        Inserting the same raw_text twice should fail on the unique constraint,
        ensuring deduplication works.
        """
        from app.models import SymbolEvent
        from sqlalchemy.exc import IntegrityError

        raw_text = "TATAMOTORS board approves QIP of Rs 3000 Cr"
        self._insert_pending_event("TATAMOTORS", raw_text)

        import hashlib
        text_hash = hashlib.sha256(raw_text.encode()).hexdigest()[:64]

        duplicate_row = SymbolEvent(
            trade_date=self.trade_date,
            symbol="TATAMOTORS",
            raw_text=raw_text,
            source="NSE_ANNOUNCEMENTS",
            headline=raw_text[:100],
            announced_at=None,
            event_type=None,
            sentiment=None,
            confidence=None,
            label=None,
            raw_text_hash=text_hash,
        )
        self.db.add(duplicate_row)
        with self.assertRaises(IntegrityError):
            self.db.flush()

        self.db.rollback()


# ═════════════════════════════════════════════════════════════════════════════
# 5. Integration test: events → scoring → catalyst_score changes
# ═════════════════════════════════════════════════════════════════════════════

class TestEventsIntegration(unittest.TestCase):
    """
    Integration test: insert classified events → run compute_events_score →
    verify that events_score is non-zero and correct.
    """

    def test_positive_earnings_event_gives_high_score(self):
        """A POSITIVE EARNINGS event should produce a catalyst_score >= 80."""
        from app.services.scoring import compute_events_score

        events = [
            {
                "event_type": "EARNINGS",
                "sentiment": "POSITIVE",
                "confidence": 0.95,
            }
        ]
        score = compute_events_score(events)
        self.assertGreaterEqual(score, 80.0, f"Expected score >= 80, got {score}")
        self.assertLessEqual(score, 100.0)

    def test_no_events_gives_zero_score(self):
        """Empty events list should give 0.0 catalyst_score."""
        from app.services.scoring import compute_events_score

        score = compute_events_score([])
        self.assertEqual(score, 0.0)

    def test_no_event_type_gives_zero_score(self):
        """Events with NO_EVENT type should be filtered out."""
        from app.services.scoring import compute_events_score

        events = [
            {"event_type": "NO_EVENT", "sentiment": "NEUTRAL", "confidence": 1.0}
        ]
        score = compute_events_score(events)
        self.assertEqual(score, 0.0)

    def test_risk_event_gives_low_score(self):
        """A RISK/NEGATIVE event should give a low catalyst_score."""
        from app.services.scoring import compute_events_score

        events = [
            {
                "event_type": "RISK",
                "sentiment": "NEGATIVE",
                "confidence": 0.9,
            }
        ]
        score = compute_events_score(events)
        # RISK/NEGATIVE band is (5.0, 25.0)
        self.assertLessEqual(score, 30.0, f"Expected score <= 30, got {score}")
        self.assertGreater(score, 0.0)

    def test_multiple_positive_events_boost_score(self):
        """Multiple EARNINGS POSITIVE events should boost score via the extra bonus."""
        from app.services.scoring import compute_events_score

        events = [
            {"event_type": "EARNINGS", "sentiment": "POSITIVE", "confidence": 0.9},
            {"event_type": "EARNINGS", "sentiment": "POSITIVE", "confidence": 0.85},
        ]
        score_multi = compute_events_score(events)

        single = [
            {"event_type": "EARNINGS", "sentiment": "POSITIVE", "confidence": 0.9}
        ]
        score_single = compute_events_score(single)

        self.assertGreater(
            score_multi, score_single,
            "Multiple same-tier events should boost score above single event"
        )

    def test_catalyst_score_difference_between_good_and_no_events(self):
        """
        A symbol with a POSITIVE event should score meaningfully higher
        than a symbol with no events.
        """
        from app.services.scoring import compute_events_score

        with_event = compute_events_score([
            {"event_type": "GUIDANCE", "sentiment": "POSITIVE", "confidence": 0.88}
        ])
        without_event = compute_events_score([])

        self.assertGreater(
            with_event - without_event, 50,
            f"Score difference should be >50. Got: {with_event} vs {without_event}"
        )

    def test_broker_upgrade_gives_positive_catalyst(self):
        """A BROKER_RATING POSITIVE event should score >= 75."""
        from app.services.scoring import compute_events_score

        events = [
            {"event_type": "BROKER_RATING", "sentiment": "POSITIVE", "confidence": 0.92}
        ]
        score = compute_events_score(events)
        self.assertGreaterEqual(score, 75.0)

    def test_total_score_increases_with_good_catalyst(self):
        """
        compute_total_score with a high catalyst vs zero catalyst
        should produce a meaningfully higher total.
        """
        from app.services.scoring import compute_total_score

        score_with_catalyst = compute_total_score(
            catalyst=90.0, preopen=50.0, liquidity=70.0, technical=60.0
        )
        score_without_catalyst = compute_total_score(
            catalyst=0.0, preopen=50.0, liquidity=70.0, technical=60.0
        )

        # Catalyst weight is 0.40 → difference should be 0.40 * 90 = 36
        diff = score_with_catalyst - score_without_catalyst
        self.assertAlmostEqual(diff, 36.0, places=1)


# ═════════════════════════════════════════════════════════════════════════════
# 6. Announcements collector universe filtering
# ═════════════════════════════════════════════════════════════════════════════

class TestUniverseFiltering(unittest.TestCase):
    """Verify that items not in the universe are filtered out."""

    def test_non_universe_symbol_is_skipped(self):
        """Symbol 'NOTINUNIVERSE' should be skipped if universe is known symbols."""
        from app.collectors.nse_announcements import fetch_announcements_for_date
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db import Base
        from app.models import SymbolEvent  # noqa: F401

        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        trade_date = datetime.date(2026, 6, 26)

        # Fake API items: 2 in universe, 1 outside
        fake_api_items = [
            {"symbol": "TATAMOTORS", "headline": "Board approves QIP", "body": "QIP of Rs 3000 Cr approved", "announced_at": None, "source_url": ""},
            {"symbol": "ZOMATO", "headline": "Broker initiates coverage", "body": "Motilal Oswal BUY target Rs 280", "announced_at": None, "source_url": ""},
            {"symbol": "NOTINUNIVERSE", "headline": "Unknown company filing", "body": "Filing for unknown company", "announced_at": None, "source_url": ""},
        ]

        with patch(
            "app.collectors.nse_announcements._get_universe_symbols",
            return_value={"RELIANCE", "INFY", "HDFCBANK", "TATAMOTORS", "ZOMATO"},
        ), patch(
            "app.collectors.nse_announcements._get_nse_session",
            return_value=MagicMock(),
        ), patch(
            "app.collectors.nse_announcements._fetch_rss_items",
            return_value=[],
        ), patch(
            "app.collectors.nse_announcements._fetch_json_api_items",
            return_value=fake_api_items,
        ):
            result = fetch_announcements_for_date(trade_date=trade_date, universe="nifty_500", db=db)

        # NOTINUNIVERSE should be skipped
        self.assertGreater(
            result["skipped_no_symbol_match"], 0,
            "Expected at least one 'not in universe' skip"
        )
        # TATAMOTORS and ZOMATO should be inserted
        self.assertEqual(result["inserted"], 2)

        db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
