"""
app/schemas.py

Pydantic schemas for FastAPI request and response validation.

These are separate from the SQLAlchemy models in models.py.
- models.py describes what is stored in the database.
- schemas.py describes what the API sends and receives as JSON.

Only the schemas needed for Milestone 1 endpoints are defined here.
More schemas will be added in later milestones.
"""

import datetime
from typing import Optional

from pydantic import BaseModel


# ── Health check response ─────────────────────────────────────────
class HealthResponse(BaseModel):
    """Response body for the GET /health endpoint."""
    status: str
    app: str
    version: str
    environment: str
    timestamp: datetime.datetime


# ── Symbol schemas ────────────────────────────────────────────────
class SymbolBase(BaseModel):
    symbol: str
    company_name: str
    series: Optional[str] = None
    sector: Optional[str] = None
    is_fno: bool = False
    avg_daily_value_20d: Optional[float] = None
    is_active: bool = True


class SymbolCreate(SymbolBase):
    """Schema for creating a new symbol record."""
    pass


class SymbolRead(SymbolBase):
    """Schema for reading a symbol record from the API."""
    id: int
    updated_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


# ── Event schemas ─────────────────────────────────────────────────
class EventBase(BaseModel):
    symbol: str
    source: str
    source_url: Optional[str] = None
    event_timestamp: Optional[datetime.datetime] = None
    headline: str
    raw_text: Optional[str] = None
    category: Optional[str] = None
    sentiment: Optional[str] = None
    priority_label: Optional[str] = None
    catalyst_score: Optional[float] = None


class EventRead(EventBase):
    """Schema for reading an event record from the API."""
    event_id: int
    ingested_at: datetime.datetime

    model_config = {"from_attributes": True}


# ── DailyRanking schemas ──────────────────────────────────────────
class DailyRankingRead(BaseModel):
    id: int
    trade_date: datetime.date
    symbol: str
    catalyst_score: Optional[float] = None
    preopen_score: Optional[float] = None
    liquidity_score: Optional[float] = None
    technical_score: Optional[float] = None
    total_score: Optional[float] = None
    rank: Optional[int] = None
    watchlist_bucket: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Brief schemas ─────────────────────────────────────────────────
class BriefRead(BaseModel):
    id: int
    trade_date: datetime.date
    generated_at: datetime.datetime
    delivery_channel: str
    brief_markdown: Optional[str] = None
    top_watchlist_json: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Milestone 4: Morning brief response schemas ───────────────────
class EventTag(BaseModel):
    """A single classified event attached to a symbol."""
    event_type: str
    sentiment: str
    label: str


class TopSymbol(BaseModel):
    """One row in the morning brief top board."""
    rank: int
    symbol: str
    bucket: str
    total_score: Optional[float] = None
    catalyst_score: Optional[float] = None
    preopen_score: Optional[float] = None
    liquidity_score: Optional[float] = None
    technical_score: Optional[float] = None
    event_tags: list[EventTag] = []


class BriefSections(BaseModel):
    """Structured sections of the morning brief."""
    top_board: list[str] = []
    positive_catalysts: list[str] = []
    risk_names: list[str] = []
    noisy_items: list[str] = []


class MorningBriefResponse(BaseModel):
    """
    Response schema for GET /brief/{trade_date}.

    Fields:
        trade_date     — the date this brief covers
        top_symbols    — top 5 symbols with scores + event tags
        sections       — structured brief sections (catalysts, risks, etc.)
        rendered_brief — full Markdown brief text ready to display
    """
    trade_date: datetime.date
    top_symbols: list[TopSymbol]
    sections: BriefSections
    rendered_brief: str


class SymbolEventRead(BaseModel):
    """Schema for reading a symbol_events row from the API."""
    id: int
    trade_date: datetime.date
    symbol: str
    event_type: Optional[str] = None
    sentiment: Optional[str] = None
    confidence: Optional[float] = None
    label: Optional[str] = None
    created_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}
