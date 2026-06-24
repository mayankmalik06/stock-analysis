#!/usr/bin/env python3
"""
scripts/seed_test_data.py

Seeds realistic sample data into the database for Milestone 3 testing.
This script is for TESTING ONLY — it creates sample symbols, events,
and pre-open snapshots covering a range of scoring scenarios.

Run from project root:
    python scripts/seed_test_data.py

The data covers these intended scoring scenarios:
  RELIANCE  — HIGH catalyst + strong pre-open + high liquidity → A-grade
  INFY      — HIGH catalyst + moderate pre-open + high liquidity → A or B
  TATAMOTORS — MEDIUM catalyst + strong pre-open + F&O → B
  ADANIENT  — HIGH catalyst only, no pre-open data → mid score
  MOTHERSON — LOW catalyst + moderate pre-open + low liquidity → C
  IRCTC     — no events, no pre-open → lowest score
  ZOMATO    — MEDIUM catalyst + low pre-open + not F&O → C
  HDFCBANK  — no events but very high liquidity + F&O → floor score
"""

import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal, create_tables
from app.models import Symbol, Event, PreopenSnapshot

TRADE_DATE = datetime.date(2026, 6, 25)
NOW = datetime.datetime(2026, 6, 25, 8, 45, 0)   # 8:45 AM IST on trade day


def seed():
    create_tables()
    db = SessionLocal()

    # ── Clear any old test data ──────────────────────────────────────
    db.query(PreopenSnapshot).delete()
    db.query(Event).delete()
    db.query(Symbol).delete()
    db.commit()
    print("Cleared old data.")

    # ── Symbols ──────────────────────────────────────────────────────
    symbols = [
        Symbol(
            symbol="RELIANCE",
            company_name="Reliance Industries Ltd",
            series="EQ",
            sector="Energy",
            is_fno=True,
            avg_daily_value_20d=2500.0,   # Very liquid, 2500 Cr/day
            is_active=True,
            in_nifty_500=True,
            in_nifty_50=True,
            is_custom_watchlist=False,
        ),
        Symbol(
            symbol="INFY",
            company_name="Infosys Ltd",
            series="EQ",
            sector="IT",
            is_fno=True,
            avg_daily_value_20d=800.0,    # Highly liquid
            is_active=True,
            in_nifty_500=True,
            in_nifty_50=True,
            is_custom_watchlist=False,
        ),
        Symbol(
            symbol="TATAMOTORS",
            company_name="Tata Motors Ltd",
            series="EQ",
            sector="Automobile",
            is_fno=True,
            avg_daily_value_20d=350.0,    # Medium-high liquidity
            is_active=True,
            in_nifty_500=True,
            in_nifty_50=False,
            is_custom_watchlist=False,
        ),
        Symbol(
            symbol="ADANIENT",
            company_name="Adani Enterprises Ltd",
            series="EQ",
            sector="Conglomerate",
            is_fno=True,
            avg_daily_value_20d=600.0,    # Good liquidity
            is_active=True,
            in_nifty_500=True,
            in_nifty_50=False,
            is_custom_watchlist=False,
        ),
        Symbol(
            symbol="MOTHERSON",
            company_name="Motherson Sumi Wiring India Ltd",
            series="EQ",
            sector="Auto Components",
            is_fno=False,
            avg_daily_value_20d=25.0,     # Low liquidity, not F&O
            is_active=True,
            in_nifty_500=True,
            in_nifty_50=False,
            is_custom_watchlist=False,
        ),
        Symbol(
            symbol="IRCTC",
            company_name="Indian Railway Catering and Tourism Corp",
            series="EQ",
            sector="Services",
            is_fno=True,
            avg_daily_value_20d=120.0,    # Moderate liquidity
            is_active=True,
            in_nifty_500=True,
            in_nifty_50=False,
            is_custom_watchlist=False,
        ),
        Symbol(
            symbol="ZOMATO",
            company_name="Zomato Ltd",
            series="EQ",
            sector="Consumer Services",
            is_fno=False,
            avg_daily_value_20d=180.0,    # Moderate liquidity, not F&O
            is_active=True,
            in_nifty_500=True,
            in_nifty_50=False,
            is_custom_watchlist=False,
        ),
        Symbol(
            symbol="HDFCBANK",
            company_name="HDFC Bank Ltd",
            series="EQ",
            sector="Financial Services",
            is_fno=True,
            avg_daily_value_20d=3000.0,   # One of the most liquid stocks
            is_active=True,
            in_nifty_500=True,
            in_nifty_50=True,
            is_custom_watchlist=False,
        ),
    ]
    db.add_all(symbols)
    db.commit()
    print(f"Inserted {len(symbols)} symbols.")

    # ── Events ───────────────────────────────────────────────────────
    events = [
        # RELIANCE — HIGH: earnings result + order win
        Event(
            symbol="RELIANCE",
            source="NSE_FILING",
            headline="Reliance Industries Q4FY26 earnings results: Net profit up 18% YoY",
            event_timestamp=NOW - datetime.timedelta(hours=8),
            ingested_at=NOW - datetime.timedelta(hours=7),
            priority_label=None,   # Let keyword classifier work
        ),
        Event(
            symbol="RELIANCE",
            source="NSE_FILING",
            headline="Reliance receives large order win for Jio 5G expansion worth Rs 5000 Cr",
            event_timestamp=NOW - datetime.timedelta(hours=6),
            ingested_at=NOW - datetime.timedelta(hours=5),
            priority_label=None,
        ),

        # INFY — HIGH: results + guidance update
        Event(
            symbol="INFY",
            source="NSE_FILING",
            headline="Infosys Q1FY27 quarterly result announced, revenue guidance raised to 8-10%",
            event_timestamp=NOW - datetime.timedelta(hours=10),
            ingested_at=NOW - datetime.timedelta(hours=9),
            priority_label=None,
        ),

        # TATAMOTORS — MEDIUM: board meeting approval
        Event(
            symbol="TATAMOTORS",
            source="NSE_RSS",
            headline="Board meeting: Tata Motors board approval for JLR refinancing plan",
            event_timestamp=NOW - datetime.timedelta(hours=15),
            ingested_at=NOW - datetime.timedelta(hours=14),
            priority_label=None,
        ),
        Event(
            symbol="TATAMOTORS",
            source="NSE_RSS",
            headline="Tata Motors fundraising via QIP announced, to raise Rs 3000 Cr",
            event_timestamp=NOW - datetime.timedelta(hours=12),
            ingested_at=NOW - datetime.timedelta(hours=11),
            priority_label=None,
        ),

        # ADANIENT — HIGH: rating upgrade
        Event(
            symbol="ADANIENT",
            source="NSE_FILING",
            headline="Credit rating upgrade by CRISIL for Adani Enterprises from AA- to AA",
            event_timestamp=NOW - datetime.timedelta(hours=5),
            ingested_at=NOW - datetime.timedelta(hours=4),
            priority_label=None,
        ),

        # MOTHERSON — LOW: generic regulatory filing
        Event(
            symbol="MOTHERSON",
            source="NSE_RSS",
            headline="Motherson Sumi general update: compliance disclosure for Q4FY26",
            event_timestamp=NOW - datetime.timedelta(hours=18),
            ingested_at=NOW - datetime.timedelta(hours=17),
            priority_label=None,
        ),

        # ZOMATO — MEDIUM: analyst report
        Event(
            symbol="ZOMATO",
            source="NSE_RSS",
            headline="Analyst initiating coverage on Zomato with target price of Rs 280",
            event_timestamp=NOW - datetime.timedelta(hours=20),
            ingested_at=NOW - datetime.timedelta(hours=19),
            priority_label=None,
        ),

        # HDFCBANK — no events (will score on liquidity only)
        # IRCTC — no events, no snapshots (will score near bottom)
    ]
    db.add_all(events)
    db.commit()
    print(f"Inserted {len(events)} events.")

    # ── Pre-open snapshots ────────────────────────────────────────────
    # Snapshot times are during the pre-open session on trade date
    t1 = datetime.datetime(2026, 6, 25, 9, 0, 0)
    t2 = datetime.datetime(2026, 6, 25, 9, 5, 0)
    t3 = datetime.datetime(2026, 6, 25, 9, 10, 0)

    snapshots = [
        # RELIANCE — strong gap up ~4.2%, high value
        PreopenSnapshot(symbol="RELIANCE", snapshot_time=t1,
                        prev_close=2950.0, indicative_price=3074.0, gap_pct=4.20,
                        buy_qty=150000, sell_qty=60000, indicative_volume=85000,
                        indicative_value=261_290_000.0),   # ~261 Cr
        PreopenSnapshot(symbol="RELIANCE", snapshot_time=t2,
                        prev_close=2950.0, indicative_price=3078.5, gap_pct=4.36,
                        buy_qty=155000, sell_qty=58000, indicative_volume=88000,
                        indicative_value=270_908_000.0),
        PreopenSnapshot(symbol="RELIANCE", snapshot_time=t3,
                        prev_close=2950.0, indicative_price=3075.0, gap_pct=4.24,
                        buy_qty=152000, sell_qty=61000, indicative_volume=86000,
                        indicative_value=264_450_000.0),

        # INFY — moderate gap up ~2.1%, medium value
        PreopenSnapshot(symbol="INFY", snapshot_time=t1,
                        prev_close=1580.0, indicative_price=1613.2, gap_pct=2.10,
                        buy_qty=80000, sell_qty=40000, indicative_volume=45000,
                        indicative_value=72_594_000.0),
        PreopenSnapshot(symbol="INFY", snapshot_time=t2,
                        prev_close=1580.0, indicative_price=1615.0, gap_pct=2.22,
                        buy_qty=82000, sell_qty=39000, indicative_volume=46000,
                        indicative_value=74_290_000.0),

        # TATAMOTORS — strong gap down ~-3.5%
        PreopenSnapshot(symbol="TATAMOTORS", snapshot_time=t1,
                        prev_close=780.0, indicative_price=752.7, gap_pct=-3.50,
                        buy_qty=90000, sell_qty=200000, indicative_volume=120000,
                        indicative_value=90_324_000.0),
        PreopenSnapshot(symbol="TATAMOTORS", snapshot_time=t2,
                        prev_close=780.0, indicative_price=754.1, gap_pct=-3.32,
                        buy_qty=92000, sell_qty=195000, indicative_volume=118000,
                        indicative_value=88_983_800.0),

        # ADANIENT — very small gap ~0.8%
        PreopenSnapshot(symbol="ADANIENT", snapshot_time=t1,
                        prev_close=2400.0, indicative_price=2419.2, gap_pct=0.80,
                        buy_qty=30000, sell_qty=28000, indicative_volume=18000,
                        indicative_value=43_545_600.0),

        # MOTHERSON — moderate gap up ~2.5%, very low indicative value
        PreopenSnapshot(symbol="MOTHERSON", snapshot_time=t1,
                        prev_close=95.0, indicative_price=97.4, gap_pct=2.53,
                        buy_qty=5000, sell_qty=3000, indicative_volume=8000,
                        indicative_value=779_200.0),   # ~7.8 Lakh — very low

        # ZOMATO — small gap ~1.1%, low-medium value
        PreopenSnapshot(symbol="ZOMATO", snapshot_time=t1,
                        prev_close=215.0, indicative_price=217.4, gap_pct=1.12,
                        buy_qty=40000, sell_qty=38000, indicative_volume=30000,
                        indicative_value=6_522_000.0),

        # HDFCBANK — flat open ~0.2%
        PreopenSnapshot(symbol="HDFCBANK", snapshot_time=t1,
                        prev_close=1720.0, indicative_price=1723.4, gap_pct=0.20,
                        buy_qty=200000, sell_qty=190000, indicative_volume=180000,
                        indicative_value=310_212_000.0),

        # IRCTC — no snapshots (not in pre-open session today)
    ]
    db.add_all(snapshots)
    db.commit()
    print(f"Inserted {len(snapshots)} pre-open snapshots.")

    db.close()
    print("\nSample data seeded successfully.")
    print(f"Trade date for test: {TRADE_DATE}")
    print("Run: python scripts/run_scoring.py --date 2026-06-25")


if __name__ == "__main__":
    seed()
