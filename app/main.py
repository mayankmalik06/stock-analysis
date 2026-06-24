"""
app/main.py

FastAPI application entry point for the Nifty Pre-Market Briefing system.

This file:
- Creates the FastAPI app instance.
- Registers startup/shutdown events (creates DB tables on first run).
- Defines the /health endpoint (used to verify the app is running).
- Includes route routers as they are added in later milestones.

To run:
    uvicorn app.main:app --reload
"""

import datetime
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import create_tables
from app.schemas import HealthResponse
from app.routers import collectors as collectors_router
from app.routers import scoring as scoring_router

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup + shutdown) ─────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs setup code before the app starts accepting requests,
    and cleanup code when the app shuts down.
    """
    # Startup
    logger.info("Starting Nifty Pre-Market Briefing app (env=%s)...", settings.app_env)
    create_tables()
    logger.info("Database tables verified / created.")

    yield  # app is running — requests are served here

    # Shutdown
    logger.info("Shutting down Nifty Pre-Market Briefing app.")


# ── App instance ──────────────────────────────────────────────────
app = FastAPI(
    title="Nifty Pre-Market Briefing",
    description=(
        "AI-assisted pre-market briefing system for Nifty 500 intraday trading. "
        "Phase 1 MVP — decision support only, no auto-trading."
    ),
    version="0.1.0",
    docs_url="/docs",       # Swagger UI at http://localhost:8000/docs
    redoc_url="/redoc",     # ReDoc UI at http://localhost:8000/redoc
    lifespan=lifespan,
)

# ── CORS (allow local browser access in development) ──────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.app_env == "development" else [],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
def health_check():
    """
    Health check endpoint.
    Returns app status, version, environment, and current timestamp.
    Use this to verify the server is running correctly.
    """
    return HealthResponse(
        status="ok",
        app="Nifty Pre-Market Briefing",
        version="0.1.0",
        environment=settings.app_env,
        timestamp=datetime.datetime.now(tz=datetime.timezone.utc),
    )


@app.get("/", tags=["System"])
def root():
    """Root endpoint — redirects to /health for convenience."""
    return {
        "message": "Nifty Pre-Market Briefing API is running.",
        "health": "/health",
        "docs": "/docs",
    }

# ── Milestone 2: Collector endpoints ─────────────────────────────
app.include_router(collectors_router.router)

# ── Milestone 3: Scoring endpoints ───────────────────────────────
app.include_router(scoring_router.router)

# ── Future routers (added in later milestones) ────────────────────
# from app.routers import symbols, events, rankings, briefs
# app.include_router(symbols.router, prefix="/symbols", tags=["Symbols"])
# app.include_router(events.router, prefix="/events", tags=["Events"])
# app.include_router(rankings.router, prefix="/rankings", tags=["Rankings"])
# app.include_router(briefs.router, prefix="/briefs", tags=["Briefs"])
