"""
app/services/ranking.py

Builds the final ranked shortlist from scored stocks.

MILESTONE: 3 (Ranking Logic)
STATUS: Stub — implementation coming in Milestone 3.

Planned behaviour:
- Take a DataFrame of symbols with total_scores.
- Sort by total_score descending.
- Assign rank integers starting from 1.
- Assign watchlist_bucket: "A" (>=75), "B" (60-74), skip below 60.
- Save results to daily_rankings table.
"""


def build_ranked_shortlist(trade_date, db_session) -> list:
    """
    Compute and save daily rankings for all active symbols.
    Returns the ranked list as a list of dicts.
    """
    raise NotImplementedError("Ranking pipeline will be implemented in Milestone 3.")
