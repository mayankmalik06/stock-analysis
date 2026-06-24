"""
app/services/scoring.py

Deterministic scoring logic for catalyst, pre-open quality, liquidity,
and technical context.

MILESTONE: 3 (Ranking Logic)
STATUS: Stub — implementation coming in Milestone 3.

Scoring formula (from spec):
  Total Score = 0.40(Catalyst) + 0.25(Pre-open Quality) + 0.20(Liquidity) + 0.15(Technical)

Each component returns a score from 0 to 100.
AI does NOT determine scores — this module does.
"""


def compute_catalyst_score(event: dict) -> float:
    """Score 0–100 based on event category, source, and priority."""
    raise NotImplementedError("Catalyst scoring will be implemented in Milestone 3.")


def compute_preopen_score(snapshots: list) -> float:
    """Score 0–100 based on gap size, buy/sell imbalance, and snapshot stability."""
    raise NotImplementedError("Pre-open scoring will be implemented in Milestone 3.")


def compute_liquidity_score(symbol_data: dict) -> float:
    """Score 0–100 based on average daily value and F&O membership."""
    raise NotImplementedError("Liquidity scoring will be implemented in Milestone 3.")


def compute_technical_score(symbol_data: dict) -> float:
    """Score 0–100 based on proximity to key levels and sector trend."""
    raise NotImplementedError("Technical scoring will be implemented in Milestone 3.")


def compute_total_score(catalyst: float, preopen: float, liquidity: float, technical: float) -> float:
    """
    Weighted total score.
    Total = 0.40 * catalyst + 0.25 * preopen + 0.20 * liquidity + 0.15 * technical
    """
    return round(
        0.40 * catalyst +
        0.25 * preopen +
        0.20 * liquidity +
        0.15 * technical,
        2,
    )
