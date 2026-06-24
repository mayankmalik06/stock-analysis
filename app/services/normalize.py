"""
app/services/normalize.py

Normalizes raw collected data before it enters the scoring pipeline.

MILESTONE: 3 (Ranking Logic)
STATUS: Stub — implementation coming in Milestone 3.

Planned behaviour:
- Standardize symbol names to NSE ticker format.
- Parse and normalize timestamps to IST-aware datetimes.
- Clean headline text (strip HTML, whitespace, encoding artifacts).
- Categorize source type (NSE_FILING, NSE_RSS, etc.).
"""


def normalize_event(raw_event: dict) -> dict:
    """Normalize a raw event dict into a clean format for scoring."""
    raise NotImplementedError("Normalizer will be implemented in Milestone 3.")
