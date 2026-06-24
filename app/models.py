"""
app/models.py

SQLAlchemy database models for the Nifty Pre-Market Briefing system.

Five tables are defined here, exactly as specified in the MVP doc:
  1. Symbol        — Nifty 500 universe (one row per stock)
  2. Event         — Corporate announcements and RSS items
  3. PreopenSnapshot — Repeated pre-open market data snapshots
  4. DailyRanking  — Daily scored and ranked shortlist
  5. Brief         — Generated morning brief sent to the user

All models inherit from Base (defined in db.py).
"""

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    Date,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# ── 1. symbols ────────────────────────────────────────────────────
class Symbol(Base):
    """
    One row per stock in the Nifty 500 universe.
    Loaded from the official NSE constituent file.
    Updated periodically when NSE rebalances the index.
    """
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # NSE ticker symbol, e.g. "RELIANCE", "INFY"
    symbol: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)

    company_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # NSE series, usually "EQ" for equity
    series: Mapped[str] = mapped_column(String(10), nullable=True)

    # Sector name from NSE, e.g. "Financial Services"
    sector: Mapped[str] = mapped_column(String(100), nullable=True)

    # True if the stock has F&O (Futures & Options) contracts — used for liquidity scoring
    is_fno: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 20-day average daily traded value in INR crores — used for liquidity scoring
    avg_daily_value_20d: Mapped[float] = mapped_column(Float, nullable=True)

    # Set to False when a stock is removed from Nifty 500
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Universe membership flags ─────────────────────────────────
    # True if this stock is a current Nifty 500 constituent
    in_nifty_500: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # True if this stock is a current Nifty 50 constituent
    in_nifty_50: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # True if this stock is in the user's custom watchlist (data/custom_watchlist.csv)
    is_custom_watchlist: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # When this row was last updated
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Symbol {self.symbol} | {self.company_name}>"


# ── 2. events ─────────────────────────────────────────────────────
class Event(Base):
    """
    One row per corporate announcement or RSS news item.

    Events are collected from:
    - NSE corporate filings page
    - NSE RSS feeds

    The AI classifier populates category, sentiment, priority_label,
    and catalyst_score in Milestone 4. They start as None.
    """
    __tablename__ = "events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # NSE ticker this event belongs to, e.g. "RELIANCE"
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Source name: "NSE_FILING", "NSE_RSS", "BSE_FILING", etc.
    source: Mapped[str] = mapped_column(String(50), nullable=False)

    # Direct URL to the original announcement or feed item
    source_url: Mapped[str] = mapped_column(String(500), nullable=True)

    # When the exchange published this announcement
    event_timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=True, index=True)

    # Short headline text from the announcement
    headline: Mapped[str] = mapped_column(String(500), nullable=False)

    # Full raw text of the announcement (may be large)
    raw_text: Mapped[str] = mapped_column(Text, nullable=True)

    # ── AI-populated fields (filled in Milestone 4) ──────────────
    # Event category: "RESULTS", "ORDER_WIN", "BOARD_ACTION", "MERGER", etc.
    category: Mapped[str] = mapped_column(String(50), nullable=True)

    # Directional sentiment: "POSITIVE", "NEGATIVE", "NEUTRAL"
    sentiment: Mapped[str] = mapped_column(String(20), nullable=True)

    # Priority label: "HIGH", "MEDIUM", "LOW"
    priority_label: Mapped[str] = mapped_column(String(20), nullable=True)

    # Score from 0.0 to 10.0 assigned by the scoring engine
    catalyst_score: Mapped[float] = mapped_column(Float, nullable=True)

    # When our system ingested this event
    ingested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Event {self.event_id} | {self.symbol} | {self.headline[:60]}>"


# ── 3. preopen_snapshots ──────────────────────────────────────────
class PreopenSnapshot(Base):
    """
    One row per pre-open data poll for each stock.

    The pre-open session runs 9:00 am – 9:15 am IST.
    We poll repeatedly (e.g. every 2 minutes) to capture
    how indicative prices and volumes evolve over the session.
    """
    __tablename__ = "preopen_snapshots"

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # NSE ticker
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Exact time this snapshot was captured
    snapshot_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)

    # Previous day's official close price
    prev_close: Mapped[float] = mapped_column(Float, nullable=True)

    # Indicative opening price during the pre-open session
    indicative_price: Mapped[float] = mapped_column(Float, nullable=True)

    # Gap percentage: (indicative_price - prev_close) / prev_close * 100
    gap_pct: Mapped[float] = mapped_column(Float, nullable=True)

    # Total buy quantity in the pre-open order book
    buy_qty: Mapped[int] = mapped_column(Integer, nullable=True)

    # Total sell quantity in the pre-open order book
    sell_qty: Mapped[int] = mapped_column(Integer, nullable=True)

    # Indicative volume (number of shares to trade at open)
    indicative_volume: Mapped[int] = mapped_column(Integer, nullable=True)

    # Indicative value in INR (indicative_price × indicative_volume)
    indicative_value: Mapped[float] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:
        return f"<PreopenSnapshot {self.symbol} @ {self.snapshot_time} gap={self.gap_pct}%>"


# ── 4. daily_rankings ─────────────────────────────────────────────
class DailyRanking(Base):
    """
    One row per stock per trading day, containing its final score and rank.

    The scoring formula (from the spec):
      Total Score = 0.40(Catalyst) + 0.25(Pre-open Quality) + 0.20(Liquidity) + 0.15(Technical)

    watchlist_bucket:
      - "A" for total_score >= 75
      - "B" for total_score 60–74
      - None / not ranked for below 60
    """
    __tablename__ = "daily_rankings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The trading date this ranking belongs to
    trade_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)

    # NSE ticker
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Component scores (each 0–100)
    catalyst_score: Mapped[float] = mapped_column(Float, nullable=True)
    preopen_score: Mapped[float] = mapped_column(Float, nullable=True)
    liquidity_score: Mapped[float] = mapped_column(Float, nullable=True)
    technical_score: Mapped[float] = mapped_column(Float, nullable=True)

    # Weighted total score
    total_score: Mapped[float] = mapped_column(Float, nullable=True)

    # 1 = highest ranked stock of the day
    rank: Mapped[int] = mapped_column(Integer, nullable=True)

    # "A", "B", or None
    watchlist_bucket: Mapped[str] = mapped_column(String(5), nullable=True)

    def __repr__(self) -> str:
        return f"<DailyRanking {self.trade_date} | {self.symbol} | rank={self.rank} score={self.total_score}>"


# ── 5. briefs ─────────────────────────────────────────────────────
class Brief(Base):
    """
    One row per generated morning brief.

    Stores the full Markdown text of the brief and the top watchlist
    as JSON so results can be reviewed and compared later.
    """
    __tablename__ = "briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The trading date this brief was for
    trade_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)

    # Exact time the brief was generated
    generated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    # Delivery channel: "TELEGRAM", "EMAIL", "DRY_RUN"
    delivery_channel: Mapped[str] = mapped_column(String(20), nullable=False)

    # Full Markdown content of the brief
    brief_markdown: Mapped[str] = mapped_column(Text, nullable=True)

    # Top watchlist stored as JSON string, e.g. '[{"symbol":"RELIANCE","rank":1,...}]'
    top_watchlist_json: Mapped[str] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Brief {self.trade_date} | {self.delivery_channel} | generated_at={self.generated_at}>"
