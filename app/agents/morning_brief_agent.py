"""
app/agents/morning_brief_agent.py

AI brief writer — generates the final pre-market morning brief.

MILESTONE: 4 (AI Layer)
STATUS: Stub — implementation coming in Milestone 4.

Responsibilities:
- Receive structured inputs (ranked list, catalysts, pre-open data).
- Call LLM API with a structured prompt.
- Return the brief as Markdown text.
- The brief includes: market context, top 5 watchlist, secondary list, risk flags.

Rules:
- AI explains the ranked list. AI does NOT produce the ranked list.
- All AI inputs must be structured and auditable.
- Never allow AI to override scores set by the scoring engine.
"""


def generate_morning_brief(brief_inputs: dict) -> str:
    """
    Generate the morning brief Markdown from structured inputs.
    Returns a Markdown string ready to send to Telegram.
    """
    raise NotImplementedError("Morning brief agent will be implemented in Milestone 4.")
