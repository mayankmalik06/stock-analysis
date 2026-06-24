"""
scripts/init_db.py

One-time script to create all database tables.

Run this ONCE after cloning the project and installing requirements:
    python scripts/init_db.py

This is safe to run multiple times — it will not delete existing data.
SQLAlchemy only creates tables that do not already exist.
"""

import sys
import os

# Make sure the project root is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db import create_tables
from app.config import settings

if __name__ == "__main__":
    print(f"Using database: {settings.database_url}")
    print("Creating tables...")
    create_tables()
    print("Done. All tables created successfully.")
