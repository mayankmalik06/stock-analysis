#!/usr/bin/env python3
"""
scripts/migrate_daily_levels.py

Safe migration script that adds the daily_levels table to an existing database.

Run this ONCE on any database created before Milestone 3.5.
Running it on a fresh database (or re-running it) is safe — it does nothing
if the table already exists.

Usage:
    python scripts/migrate_daily_levels.py

What it does:
    1. Connects to the database configured in your .env file.
    2. Checks whether the daily_levels table already exists.
    3. If it does not exist, creates it with the correct schema.
    4. If it already exists, reports that and exits without touching anything.

This is the correct pattern for SQLite migrations in Phase 1.
Alembic is not required yet for a single-table addition.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text
from app.db import engine, create_tables
import app.models  # noqa: F401 — registers all models including DailyLevel


def main():
    print("=" * 60)
    print("  Milestone 3.5 — daily_levels migration")
    print("=" * 60)

    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    if "daily_levels" in existing_tables:
        print("\n  daily_levels table already exists. Nothing to do.")
        print("  Your database is up to date for Milestone 3.5.\n")
        return

    print("\n  daily_levels table not found. Creating it now...")

    # create_tables() calls Base.metadata.create_all() which only creates
    # tables that do not yet exist — safe to call on an existing database.
    create_tables()

    # Verify
    inspector2 = inspect(engine)
    if "daily_levels" in inspector2.get_table_names():
        cols = [c["name"] for c in inspector2.get_columns("daily_levels")]
        print(f"\n  Created daily_levels with columns: {cols}")
        print("\n  Migration complete. Database is ready for Milestone 3.5.\n")
    else:
        print("\n  ERROR: daily_levels was not created. Check models.py.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
