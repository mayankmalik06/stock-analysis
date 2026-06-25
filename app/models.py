"""
app/models.py

SQLAlchemy database models for the Nifty Pre-Market Briefing system.

Six tables are defined here:
  1. Symbol           — Nifty 500 universe (one row per stock)
  2. Event            — Corporate announcements and RSS items
  3. PreopenSnapshot  — Repeated pre-open market data snapshots
  4. DailyLevel       — Previous-day OHLC levels per symbol per date  ← NEW (M3.5)
  5. DailyRanking     — Daily scored and ranked shortlist
  6. Brief            — Generated morning brief sent to the user

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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# -- 1. symbols ----------------------------------------------------------------
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

    # True if the stock has F&O (Futures & Options) contracts
    is_fno: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 20-day average daily traded value in INR crores
    avg_daily_value_20d: Mapped[float] = mapped_column(Float, nullable=True)

    # Set to False when a stock is removed from Nifty 500
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Universe membership flags
    in_nifty_500: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    in_nifty_50: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_custom_watchlist: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Symbol {self.symbol} | {self.company_name}>"


# -- 2. events -----------------------------------------------------------------
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
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=True)
    event_timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=True, index=True)
    headline: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=True)

    # AI-populated fields (filled in Milestone 4)
    category: Mapped[str] = mapped_column(String(50), nullable=True)
    sentiment: Mapped[str] = mapped_column(String(20), nullable=True)
    priority_label: Mapped[str] = mapped_column(String(20), nullable=True)
    catalyst_score: Mapped[float] = mapped_column(Float, nullable=True)

    ingested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Event {self.event_id} | {self.symbol} | {self.headline[:60]}>"


# -- 3. preopen_snapshots -------------------------------------------------------
class PreopenSnapshot(Base):
    """
    One row per pre-open data poll for each stock.

    The pre-open session runs 9:00 am to 9:15 am IST.
    We poll repeatedly (e.g. every 2 minutes) to capture
    how indicative prices and volumes evolve over the session.
    """
    __tablename__ = "preopen_snapshots"

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    snapshot_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    prev_close: Mapped[float] = mapped_column(Float, nullable=True)
    indicative_price: Mapped[float] = mapped_column(Float, nullable=True)
    gap_pct: Mapped[float] = mapped_column(Float, nullable=True)
    buy_qty: Mapped[int] = mapped_column(Integer, nullable=True)
    sell_qty: Mapped[int] = mapped_column(Integer, nullable=True)
    indicative_volume: Mapped[int] = mapped_column(Integer, nullable=True)
    indicative_value: Mapped[float] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:
        return f"<PreopenSnapshot {self.symbol} @ {self.snapshot_time} gap={self.gap_pct}%>"


# -- 4. daily_levels  (NEW — Milestone 3.5) ------------------------------------
class DailyLevel(Base):
    """
    Previous-day OHLC levels for each symbol, keyed by trade_date.

    For a given trade_date (e.g. 2026-06-25), this row stores the
    high, low, and close from the PREVIOUS trading session
    (i.e. the session on 2026-06-24).

    These are used by the Technical scorer to determine whether the
    current indicative/pre-open price is in a breakout, breakdown,
    or inside-range zone relative to known structural price levels.

    The unique constraint on (trade_date, symbol) means only one row
    per stock per day. Running the loader again overwrites the existing row.

    Columns:
        trade_date  — the DATE we are trading / scoring (NOT the level date)
        symbol      — NSE ticker
        prev_high   — high of the previous trading session
        prev_low    — low of the previous trading session
        prev_close  — close of the previous trading session
        source      — where the data came from, e.g. "NSE_EOD" or "SEED"
        loaded_at   — when this row was written
    """
    __tablename__ = "daily_levels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The trading date we are scoring (NOT the date of the OHLC bar)
    trade_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)

    # NSE ticker
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Previous session high, low, close
    prev_high: Mapped[float] = mapped_column(Float, nullable=False)
    prev_low: Mapped[float] = mapped_column(Float, nullable=False)
    prev_close: Mapped[float] = mapped_column(Float, nullable=False)

    # Data source tag — helps audit where levels came from
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="UNKNOWN")

    # Timestamp when this row was loaded
    loaded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )

    # One row per (trade_date, symbol) — upsert pattern replaces on reload
    __table_args__ = (
        UniqueConstraint("trade_date", "symbol", name="uq_daily_levels_date_symbol"),
    )

    def __repr__(self) -> str:
        return (
            f"<DailyLevel {self.trade_date} | {self.symbol} "
            f"H={self.prev_high} L={self.prev_low} C={self.prev_close}>"
        )


# -- 5. symbol_events  (UPDATED — Milestone 6) --------------------------------
class SymbolEvent(Base):
    """
    One row per AI-classified event for a symbol on a specific trade date.

    Populated by:
      - scripts/load_announcements.py  -> fetches live NSE announcements, stores
        rows with event_type=None (pending classification)
      - scripts/classify_events.py     -> reads pending rows, calls the LLM
        classifier, updates event_type / sentiment / confidence / label

    Milestone 6 additions (columns added):
        source        — where the event came from (NSE_ANNOUNCEMENTS or SEED)
        headline      — short headline text (kept separate from full raw_text)
        announced_at  — original announcement timestamp from the source feed

    The unique constraint on (trade_date, symbol, raw_text_hash) prevents
    duplicate rows when the loader or classifier runs multiple times.

    Columns:
        trade_date   — the trading date this event relates to
        symbol       — NSE ticker
        raw_text     — the original headline + description sent to the LLM
        source       — data source tag, e.g. NSE_ANNOUNCEMENTS or SEED
        headline     — short headline (max 500 chars)
        announced_at — timestamp of the original announcement
        event_type   — one of: EARNINGS, GUIDANCE, BROKER_RATING, MACRO,
                        CORPORATE_ACTION, FLOW, RISK, GENERAL_NEWS, NO_EVENT
                        (NULL = pending classification)
        sentiment    — POSITIVE, NEGATIVE, or NEUTRAL  (NULL = pending)
        confidence   — LLM confidence 0.0–1.0  (NULL = pending)
        label        — user-facing one-line description of the event
        created_at   — when this row was written
    """
    __tablename__ = "symbol_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    trade_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Milestone 6: provenance fields
    source: Mapped[str] = mapped_column(String(50), nullable=True, default="SEED")
    headline: Mapped[str] = mapped_column(String(500), nullable=True)
    announced_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=True)

    # AI-classified fields (NULL = not yet classified)
    event_type: Mapped[str] = mapped_column(String(30), nullable=True)
    sentiment: Mapped[str] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    label: Mapped[str] = mapped_column(String(300), nullable=True)

    # A short hash of raw_text used for the unique constraint
    # (SQLite TEXT columns are not indexable at a fixed prefix length the same way
    #  as MySQL, so we store an explicit hash column instead)
    raw_text_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "trade_date", "symbol", "raw_text_hash",
            name="uq_symbol_events_date_symbol_hash"
        ),
    )

    def __repr__(self) -> str:
        classified = self.event_type or "PENDING"
        return (
            f"<SymbolEvent {self.trade_date} | {self.symbol} | "
            f"{classified} | {self.sentiment}>"
        )


# -- 6. daily_rankings ---------------------------------------------------------
class DailyRanking(Base):
    """
    One row per stock per trading day, containing its final score and rank.

    The scoring formula (from the spec):
      Total Score = 0.40(Catalyst) + 0.25(Pre-open Quality) + 0.20(Liquidity) + 0.15(Technical)

    watchlist_bucket:
      - "A" for total_score >= 70
      - "B" for total_score 50-69
      - "C" for total_score < 50
    """
    __tablename__ = "daily_rankings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Component scores (each 0-100)
    catalyst_score: Mapped[float] = mapped_column(Float, nullable=True)
    preopen_score: Mapped[float] = mapped_column(Float, nullable=True)
    liquidity_score: Mapped[float] = mapped_column(Float, nullable=True)
    technical_score: Mapped[float] = mapped_column(Float, nullable=True)

    # Weighted total score
    total_score: Mapped[float] = mapped_column(Float, nullable=True)

    # 1 = highest ranked stock of the day
    rank: Mapped[int] = mapped_column(Integer, nullable=True)

    # "A", "B", or "C"
    watchlist_bucket: Mapped[str] = mapped_column(String(5), nullable=True)

    def __repr__(self) -> str:
        return f"<DailyRanking {self.trade_date} | {self.symbol} | rank={self.rank} score={self.total_score}>"


# -- 7. briefs -----------------------------------------------------------------
class Brief(Base):
    """
    One row per generated morning brief.

    Stores the full Markdown text of the brief and the top watchlist
    as JSON so results can be reviewed and compared later.
    """
    __tablename__ = "briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    generated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    delivery_channel: Mapped[str] = mapped_column(String(20), nullable=False)
    brief_markdown: Mapped[str] = mapped_column(Text, nullable=True)
    top_watchlist_json: Mapped[str] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Brief {self.trade_date} | {self.delivery_channel} | generated_at={self.generated_at}>"
