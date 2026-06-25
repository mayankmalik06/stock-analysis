#!/usr/bin/env python3
"""
scripts/migrate_m6_symbol_events.py

Milestone 6 database migration.

Adds three new columns to the symbol_events table:
    source        TEXT  — data source tag (default 'SEED')
    headline      TEXT  — short headline text
    announced_at  TEXT  — original announcement timestamp

Safe to run multiple times — uses ALTER TABLE IF NOT EXISTS pattern via
a column-existence check first.

Run from project root:
    python scripts/migrate_m6_symbol_events.py
"""

import sys
import os
import sqlite3
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_db_path() -> str:
    """Locate the SQLite database from .env or use the default path."""
    from app.config import settings
    db_url = settings.database_url
    # Strip "sqlite:///" prefix
    if db_url.startswith("sqlite:///"):
        return db_url[len("sqlite:///"):]
    elif db_url.startswith("sqlite://"):
        return db_url[len("sqlite://"):]
    return "data/nifty_premarket.db"


def column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """Return True if column already exists in table."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def main():
    db_path = get_db_path()
    if not os.path.exists(db_path):
        logger.error("Database not found at %s. Run scripts/init_db.py first.", db_path)
        sys.exit(1)

    logger.info("Running Milestone 6 migration on: %s", db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    migrations = [
        ("source",       "TEXT DEFAULT 'SEED'"),
        ("headline",     "TEXT"),
        ("announced_at", "TEXT"),   # stored as ISO string; SQLAlchemy reads it as datetime
    ]

    any_change = False
    for col_name, col_def in migrations:
        if column_exists(cursor, "symbol_events", col_name):
            logger.info("Column '%s' already exists — skipping.", col_name)
        else:
            sql = f"ALTER TABLE symbol_events ADD COLUMN {col_name} {col_def}"
            logger.info("Adding column: %s", sql)
            cursor.execute(sql)
            any_change = True

    if any_change:
        conn.commit()
        logger.info("Migration committed.")
    else:
        logger.info("No changes needed — all columns already present.")

    conn.close()
    print("\nMigration complete. symbol_events now has: source, headline, announced_at")


if __name__ == "__main__":
    main()
