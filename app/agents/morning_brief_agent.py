"""
app/agents/morning_brief_agent.py

Backward-compatibility shim.
The real implementation lives in app/ai/morning_brief.py (Milestone 4).

Import from app.ai.morning_brief in new code.
"""
from app.ai.morning_brief import generate_brief  # noqa: F401

__all__ = ["generate_brief"]
