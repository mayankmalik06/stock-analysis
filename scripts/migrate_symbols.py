"""
scripts/migrate_symbols.py

Safe migration that adds the three universe-flag columns to the existing
symbols table if they don't already exist.

SQLite does not support ALTER TABLE ... ADD COLUMN IF NOT EXISTS, so we
check the existing columns first and only run the ALTER TABLE when needed.

Run this once after pulling Milestone 2 code onto your machine:
    python scripts/migrate_symbols.py
"""

import sys
import os

# Make sure the project root is on the Python path so we can import app.*
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import engine, create_tables
from sqlalchemy import text, inspect

NEW_COLUMNS = [
    ("in_nifty_500",        "BOOLEAN NOT NULL DEFAULT 0"),
    ("in_nifty_50",         "BOOLEAN NOT NULL DEFAULT 0"),
    ("is_custom_watchlist", "BOOLEAN NOT NULL DEFAULT 0"),
]


def migrate():
    # Ensure all tables exist first (creates them if this is a fresh DB)
    create_tables()
    inspector = inspect(engine)

    # Get existing column names in the symbols table
    existing = {col["name"] for col in inspector.get_columns("symbols")}
    print(f"Existing columns in symbols: {sorted(existing)}")

    with engine.begin() as conn:
        for col_name, col_def in NEW_COLUMNS:
            if col_name in existing:
                print(f"  [SKIP]  {col_name} already exists")
            else:
                sql = f"ALTER TABLE symbols ADD COLUMN {col_name} {col_def}"
                conn.execute(text(sql))
                print(f"  [ADDED] {col_name}")

    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
