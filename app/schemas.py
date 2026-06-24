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
