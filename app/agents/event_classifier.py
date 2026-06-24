"""
app/agents/event_classifier.py

AI-powered event classifier — classifies corporate announcements.

MILESTONE: 4 (AI Layer)
STATUS: Stub — implementation coming in Milestone 4.

Responsibilities:
- Take headline + raw_text + symbol metadata as structured input.
- Call LLM API with a structured prompt.
- Return: category, sentiment, priority_label, one-sentence summary.
- Update the events table with classification results.

Rules:
- AI classifies events. AI does NOT rank stocks.
- Always validate output shape before saving.
- Never invent facts not present in the input.
"""


def classify_event(event: dict) -> dict:
    """
    Classify a single event using the LLM.
    Returns: {"category": ..., "sentiment": ..., "priority_label": ..., "summary": ...}
    """
    raise NotImplementedError("Event classifier will be implemented in Milestone 4.")
