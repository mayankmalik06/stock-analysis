"""
app/db.py

Database connection setup using SQLAlchemy.

- Creates the SQLite engine from the DATABASE_URL in settings.
- Provides a SessionLocal factory for getting a database session.
- Provides the Base class that all models inherit from.
- Provides a create_tables() function used at startup to build tables.

Usage (in a FastAPI route):
    from app.db import get_db
    db = next(get_db())

Usage (at startup):
    from app.db import create_tables
    create_tables()
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings


# ── Engine ────────────────────────────────────────────────────────
# connect_args is required for SQLite to work safely with FastAPI's
# async threading model. Remove this line when migrating to PostgreSQL.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # SQLite-only setting
    echo=(settings.app_env == "development"),   # log SQL in dev mode
)

# ── Session factory ───────────────────────────────────────────────
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# ── Declarative base ──────────────────────────────────────────────
class Base(DeclarativeBase):
    """All SQLAlchemy models inherit from this Base."""
    pass


# ── Dependency for FastAPI routes ─────────────────────────────────
def get_db():
    """
    Yields a database session and closes it when the request finishes.
    Use this as a FastAPI dependency injection.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Table creation ────────────────────────────────────────────────
def create_tables():
    """
    Creates all database tables defined in models.py.
    Safe to call on every startup — only creates tables that don't exist.
    """
    # Import models here so SQLAlchemy knows about them before creating tables.
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
