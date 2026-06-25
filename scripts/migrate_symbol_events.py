#!/usr/bin/env python3
"""
scripts/migrate_symbol_events.py

Idempotent migration: creates the symbol_events table if it does not exist.

Safe to run multiple times — it checks whether the table already exists
before attempting to create it, so re-running never drops or damages data.

Run from project root:
    python scripts/migrate_symbol_events.py

Milestone: 4 (AI Layer)
"""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text
from app.db import engine, create_tables
from app.models import SymbolEvent  # ensures the model is registered with Base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def migrate():
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    if "symbol_events" in existing_tables:
        logger.info("symbol_events table already exists — no migration needed.")
        return

    logger.info("symbol_events table not found — creating it now.")
    create_tables()  # creates ALL tables that don't yet exist (safe, uses checkfirst=True)
    logger.info("symbol_events table created successfully.")

    # Verify
    inspector2 = inspect(engine)
    if "symbol_events" in inspector2.get_table_names():
        logger.info("Verification passed: symbol_events is present in the database.")
    else:
        logger.error("Verification FAILED: symbol_events was not created.")
        sys.exit(1)


if __name__ == "__main__":
    migrate()
