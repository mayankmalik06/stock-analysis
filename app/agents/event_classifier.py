"""
app/agents/event_classifier.py

Backward-compatibility shim.
The real implementation lives in app/ai/event_classifier.py (Milestone 4).

Import from app.ai.event_classifier in new code.
"""
from app.ai.event_classifier import classify_event  # noqa: F401

__all__ = ["classify_event"]
