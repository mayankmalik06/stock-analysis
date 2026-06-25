"""
app/ai/

AI components for the Nifty Pre-Market Briefing system.

Modules:
    event_classifier   — classifies symbol-level events via LLM
    morning_brief      — generates the morning brief text and JSON via LLM

Rules:
    - AI classifies events and writes the brief.
    - AI never determines rankings or scores directly.
    - All AI inputs are structured and auditable.
    - If LLM_API_KEY is missing, both modules fall back to mock/dry-run mode.
"""
