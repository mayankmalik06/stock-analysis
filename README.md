# Nifty Pre-Market Briefing — Phase 1 MVP

A personal, AI-assisted pre-market briefing system for intraday trading on Nifty 500.

The system wakes up early, scans NSE announcements and RSS feeds, collects
pre-open data, ranks the most interesting stocks of the day, and sends you
a concise briefing before the market opens at 9:15 am IST.

**This is a decision-support tool, not an auto-trading system.**

---

## What is built so far (Milestone 1)

| What | Status |
|---|---|
| Project folder structure | Done |
| FastAPI application with /health endpoint | Done |
| SQLite database with all 5 tables | Done |
| Configuration via environment variables | Done |
| requirements.txt with all Phase 1 packages | Done |
| .env.example listing all required secrets | Done |
| Stubs for collectors, services, agents, delivery, jobs | Done |

Data collection, scoring, AI, and Telegram delivery are coming in later milestones.

---

## Requirements

- **Python 3.12** — [Download here](https://www.python.org/downloads/)
- A terminal (Command Prompt, PowerShell, or Terminal on Mac/Linux)
- No other tools needed for Milestone 1

---

## Setup instructions (step by step)

### Step 1 — Get the project files

If you received a zip file, unzip it. You should have a folder called `nifty_premarket`.

Open your terminal and navigate into that folder:

```bash
cd nifty_premarket
```

---

### Step 2 — Create a virtual environment

A virtual environment keeps this project's packages separate from everything else on your computer.

**On Mac or Linux:**
```bash
python3.12 -m venv venv
```

**On Windows:**
```bash
py -3.12 -m venv venv
```

---

### Step 3 — Activate the virtual environment

You must activate it every time you open a new terminal before running the project.

**On Mac or Linux:**
```bash
source venv/bin/activate
```

**On Windows:**
```bash
venv\Scripts\activate
```

After activation, your terminal prompt will show `(venv)` at the start.

---

### Step 4 — Install dependencies

```bash
pip install -r requirements.txt
```

This will download and install all required packages. It may take 1–2 minutes.

---

### Step 5 — Create your environment file

Copy the example environment file:

**On Mac or Linux:**
```bash
cp .env.example .env
```

**On Windows:**
```bash
copy .env.example .env
```

Open the `.env` file in any text editor. You do not need to fill in anything for Milestone 1.
Real secrets (Telegram token, LLM key) will be added in later milestones.

---

### Step 6 — Create the database tables (first time only)

```bash
python scripts/init_db.py
```

You should see output like:
```
Using database: sqlite:///./data/nifty_premarket.db
Creating tables...
Done. All tables created successfully.
```

A file called `nifty_premarket.db` will appear inside the `data/` folder.

---

### Step 7 — Start the FastAPI app

```bash
uvicorn app.main:app --reload
```

You should see output like:
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Database tables verified / created.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

Leave this terminal open while you test.

---

### Step 8 — Verify the app is running

Open your browser and go to:

```
http://127.0.0.1:8000/health
```

You should see a JSON response like:
```json
{
  "status": "ok",
  "app": "Nifty Pre-Market Briefing",
  "version": "0.1.0",
  "environment": "development",
  "timestamp": "2026-06-25T03:00:00Z"
}
```

You can also explore the interactive API documentation at:
```
http://127.0.0.1:8000/docs
```

---

### Step 9 — Run the tests (optional but recommended)

Open a second terminal, activate the virtual environment again, and run:

```bash
pytest tests/ -v
```

All three tests should pass.

---

## How to stop the app

Press `Ctrl + C` in the terminal where uvicorn is running.

---

## Project structure

```
nifty_premarket/
│
├── app/
│   ├── main.py              ← FastAPI app entry point
│   ├── config.py            ← Settings from environment variables
│   ├── db.py                ← Database connection (SQLAlchemy)
│   ├── models.py            ← Database table definitions (5 tables)
│   ├── schemas.py           ← API response format definitions
│   │
│   ├── collectors/          ← Data collection modules (Milestone 2)
│   │   ├── universe.py      ← Nifty 500 universe loader
│   │   ├── nse_announcements.py  ← NSE corporate filings
│   │   ├── nse_rss.py       ← NSE RSS feeds
│   │   └── preopen.py       ← Pre-open market data
│   │
│   ├── services/            ← Processing logic (Milestone 3)
│   │   ├── normalize.py     ← Data normalization
│   │   ├── scoring.py       ← Deterministic stock scoring
│   │   ├── ranking.py       ← Ranked shortlist builder
│   │   └── brief_builder.py ← Prepares inputs for AI brief
│   │
│   ├── agents/              ← AI modules (Milestone 4)
│   │   ├── event_classifier.py   ← Classifies announcements
│   │   └── morning_brief_agent.py ← Writes the morning brief
│   │
│   ├── delivery/            ← Output channels (Milestone 5)
│   │   ├── telegram.py      ← Telegram message sender
│   │   └── email.py         ← Email backup channel
│   │
│   └── jobs/                ← Scheduler (Milestone 5)
│       └── scheduler.py     ← APScheduler morning workflow
│
├── tests/
│   └── test_health.py       ← Basic app verification tests
│
├── scripts/
│   └── init_db.py           ← One-time database setup script
│
├── data/                    ← SQLite database lives here (not committed to git)
│
├── requirements.txt         ← All Python package dependencies
├── .env.example             ← Template for your secrets file
├── .gitignore               ← Files not committed to git
└── README.md                ← This file
```

---

## Database tables

| Table | What it stores |
|---|---|
| `symbols` | All Nifty 500 stocks with sector and liquidity info |
| `events` | Corporate announcements and RSS news items |
| `preopen_snapshots` | Pre-open price and volume snapshots (9:00–9:15 am) |
| `daily_rankings` | Daily scored and ranked shortlist |
| `briefs` | Final morning brief sent each day |

---

## Daily schedule (when fully built)

| Time (IST) | What happens |
|---|---|
| 6:00 am | Overnight announcements collected |
| 7:00 am | RSS and announcement refresh |
| 8:00 am | AI classification of events |
| 8:45 am | Preliminary ranked list built |
| 9:00–9:14 am | Pre-open data polled repeatedly |
| 9:14 am | Final rankings frozen, brief generated |
| 9:15 am | Brief delivered to Telegram |

---

## Configuration variables

See `.env.example` for the full list. Key variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Path to the SQLite database file |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID (from @userinfobot) |
| `LLM_API_KEY` | API key for the LLM (used in Milestone 4) |
| `APP_ENV` | Set to `development` locally, `production` on server |
| `LOG_LEVEL` | Set to `DEBUG` for verbose logs, `INFO` for normal |

---

## Build milestones

| Milestone | What | Status |
|---|---|---|
| 1 — Foundation | Project structure, database, FastAPI skeleton | **Done** |
| 2 — Source ingestion | NSE announcements, RSS, pre-open, universe | Pending |
| 3 — Ranking logic | Scoring engine, ranked shortlist | Pending |
| 4 — AI layer | Event classifier, brief writer | Pending |
| 5 — Delivery | Telegram, scheduler | Pending |
| 6 — Hardening | Logging, retries, validation, tests | Pending |
