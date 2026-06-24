"""
app/services/brief_builder.py

Assembles structured inputs for the AI brief writer.

MILESTONE: 4 (AI Layer)
STATUS: Stub — implementation coming in Milestone 4.

Planned behaviour:
- Pull top-ranked symbols for the day.
- Pull their events, pre-open snapshots, and scores.
- Format everything into a clean structured dict for the AI agent.
- Pass to morning_brief_agent to generate Markdown brief.
- Save the result to the briefs table.
"""


def build_brief_inputs(trade_date, db_session) -> dict:
    """Assemble structured inputs needed by the AI brief writer."""
    raise NotImplementedError("Brief builder will be implemented in Milestone 4.")
