# Nifty Pre-Market Briefing — Phase 1 MVP

A personal, AI-assisted pre-market briefing system for intraday trading on Nifty 500.

The system wakes up early, scans NSE announcements and RSS feeds, collects
pre-open data, ranks the most interesting stocks of the day, and sends you
a concise briefing before the market opens at 9:15 am IST.

**This is a decision-support tool, not an auto-trading system.**

---

## What is built so far

| Milestone | What | Status |
|---|---|---|
| 1 — Foundation | Project structure, database, FastAPI skeleton | **Done** |
| 2 — Source ingestion | NSE announcements, RSS, pre-open, universe | **Done** |
| 3 — Ranking logic | Scoring engine, ranked shortlist | **Done** |
| 3.5 — Daily OHLC levels | prev_high/prev_low storage + improved Technical scoring | **Done** |
| 4 — AI layer | Event classifier, brief writer | Pending |
| 5 — Delivery | Telegram, scheduler | Pending |
| 6 — Hardening | Logging, retries, validation, tests | Pending |

### Milestone 3.5 additions

| What | Status |
|---|---|
| `daily_levels` table — stores prev_high, prev_low, prev_close per symbol per date | Done |
| `scripts/migrate_daily_levels.py` — safe migration for existing databases | Done |
| `scripts/load_daily_levels.py` — CLI loader for previous-day OHLC levels | Done |
| Technical scorer updated — uses prev_high/prev_low to detect breakout/breakdown zones | Done |
| Graceful fallback — gap%-based Technical score when levels are not loaded | Done |
| New unit tests in `tests/test_scoring.py` covering 10 Technical scoring scenarios | Done |

---

## Requirements

- **Python 3.12** — [Download here](https://www.python.org/downloads/)
- A terminal (Command Prompt, PowerShell, or Terminal on Mac/Linux)

---

## Setup instructions (step by step)

### Step 1 — Get the project files

```bash
cd stock-analysis
```

### Step 2 — Create a virtual environment

**On Mac or Linux:**
```bash
python3.12 -m venv venv
source venv/bin/activate
```

**On Windows:**
```bash
py -3.12 -m venv venv
venv\Scripts\activate
```

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4 — Create your environment file

```bash
cp .env.example .env
```

Open `.env` in any text editor. You do not need to fill in anything for local testing.

### Step 5 — Create the database tables (first time only)

```bash
python scripts/init_db.py
```

Expected output:
```
Using database: sqlite:///./data/nifty_premarket.db
Creating tables...
Done. All tables created successfully.
```

**If you have an existing database from before Milestone 3.5**, run the migration instead:
```bash
python scripts/migrate_daily_levels.py
```
This safely adds the `daily_levels` table without touching any existing data.

### Step 6 — Start the FastAPI app

```bash
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000/health` to verify the app is running.
Visit `http://127.0.0.1:8000/docs` for the interactive API docs.

### Step 7 — Run the tests

```bash
pytest tests/ -v
```

All tests should pass (including the new Technical scoring tests in `test_scoring.py`).

---

## Milestone 3.5 — Loading daily OHLC levels

Before running scoring with real Technical context, you need to load previous-day
OHLC levels for the symbols you want to score.

### Load levels for a specific date

```bash
# Derive levels from existing pre-open snapshots (default mode)
python scripts/load_daily_levels.py --date 2026-06-25

# Use seed/test data (no network needed — useful for local demos)
python scripts/load_daily_levels.py --date 2026-06-25 --mode seed

# Load for Nifty 50 only
python scripts/load_daily_levels.py --date 2026-06-25 --universe nifty_50
```

### Run scoring (uses levels automatically when available)

```bash
# Score today
python scripts/run_scoring.py

# Score a specific date
python scripts/run_scoring.py --date 2026-06-25

# Show only A-grade results (total_score >= 70)
python scripts/run_scoring.py --date 2026-06-25 --show-bucket A

# Show top 10
python scripts/run_scoring.py --date 2026-06-25 --top 10
```

### Query results in SQLite

```bash
sqlite3 data/nifty_premarket.db
```

```sql
-- View scored rankings with Technical scores
SELECT rank, symbol, technical_score, total_score, watchlist_bucket
FROM daily_rankings
WHERE trade_date = '2026-06-25'
ORDER BY rank
LIMIT 20;

-- View loaded levels
SELECT symbol, prev_high, prev_low, prev_close, source
FROM daily_levels
WHERE trade_date = '2026-06-25'
LIMIT 20;
```

---

## How the Technical score works (Milestone 3.5)

The Technical score (0–100, weight 15% of total) tells you where today's
indicative pre-open price sits relative to yesterday's trading range.

**When previous-day levels are loaded:**

| Price position | What it means | Technical score |
|---|---|---|
| Above prev_high | Breakout — price cleared yesterday's high | 70–100 |
| Below prev_low | Breakdown — price broke below yesterday's low | 70–100 |
| Inside range, near top 20% | Approaching the high, not yet broken out | 55–69 |
| Inside range, near bottom 20% | Near support, not yet broken down | 55–69 |
| Inside range, middle 60% | No clear structural context | 20–54 |

**When levels are not loaded** (fallback):
Uses gap% as a proxy. Large gaps (>5%) get 80+, small gaps get lower scores.

**Plain language example:**

- RELIANCE opens at ₹2,520 with yesterday's range H=2,500 / L=2,400:
  → Above prev_high by 0.8% → **Breakout** → Technical score ~72

- INFY opens at ₹1,710 with yesterday's range H=1,800 / L=1,720:
  → Below prev_low by 0.58% → **Breakdown** → Technical score ~72

- TCS opens at ₹4,160 with yesterday's range H=4,200 / L=4,050:
  → Inside range, position at 73% of range (near top) → Technical score ~62

- WIPRO opens at ₹575 with yesterday's range H=590 / L=560:
  → Inside range, dead center → Technical score ~35

---

## How the daily schedule will work (when fully built)

| Time (IST) | What happens |
|---|---|
| 6:00 am | Overnight announcements collected |
| 7:00 am | RSS and announcement refresh |
| 8:00 am | AI classification of events |
| 8:30 am | **Load daily OHLC levels** (`load_daily_levels.py`) |
| 8:45 am | Preliminary ranked list built (with improved Technical scores) |
| 9:00–9:14 am | Pre-open data polled repeatedly |
| 9:14 am | Final rankings frozen, brief generated |
| 9:15 am | Brief delivered to Telegram |

---

## Project structure

```
stock-analysis/
│
├── app/
│   ├── main.py              ← FastAPI app entry point
│   ├── config.py            ← Settings from environment variables
│   ├── db.py                ← Database connection (SQLAlchemy)
│   ├── models.py            ← Database table definitions (6 tables)
│   ├── schemas.py           ← API response format definitions
│   │
│   ├── collectors/          ← Data collection modules (Milestone 2)
│   │   ├── universe.py      ← Nifty 500 universe loader
│   │   ├── nse_announcements.py  ← NSE corporate filings
│   │   ├── nse_rss.py       ← NSE RSS feeds
│   │   └── preopen.py       ← Pre-open market data
│   │
│   ├── services/            ← Processing logic (Milestones 3 & 3.5)
│   │   ├── normalize.py     ← Data normalization
│   │   ├── scoring.py       ← Deterministic stock scoring (updated M3.5)
│   │   ├── ranking.py       ← Ranked shortlist builder (updated M3.5)
│   │   └── brief_builder.py ← Prepares inputs for AI brief
│   │
│   ├── agents/              ← AI modules (Milestone 4)
│   ├── delivery/            ← Output channels (Milestone 5)
│   └── jobs/                ← Scheduler (Milestone 5)
│
├── tests/
│   ├── test_health.py       ← Basic app verification tests
│   └── test_scoring.py      ← Technical scoring unit tests (new M3.5)
│
├── scripts/
│   ├── init_db.py           ← One-time database setup script
│   ├── migrate_symbols.py   ← Safe migration: adds universe flag columns
│   ├── migrate_daily_levels.py  ← Safe migration: adds daily_levels table (M3.5)
│   ├── load_nifty500.py     ← Load Nifty 500 universe from NSE
│   ├── load_daily_levels.py ← Load previous-day OHLC levels (M3.5)
│   ├── run_rss.py           ← Run NSE RSS / news feed collector
│   ├── run_announcements.py ← Run NSE corporate announcements collector
│   ├── run_preopen.py       ← Run pre-open collector (use --test flag)
│   └── run_scoring.py       ← Run the scoring pipeline
│
├── data/                    ← SQLite database lives here (not committed to git)
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
| `daily_levels` | Previous-day high, low, close per symbol per date **(new M3.5)** |
| `daily_rankings` | Daily scored and ranked shortlist |
| `briefs` | Final morning brief sent each day |

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
| `DATA_MODE` | `simulated` (default) or `live` — controls EOD and pre-open data source (new M5) |

---

## Milestone 5 — Live NSE Data Integration

Milestone 5 replaces the seed/approximation flows with real NSE data
for daily OHLC levels and pre-open prices.

### What changed in Milestone 5

| Component | Before M5 | After M5 |
|---|---|---|
| `load_daily_levels.py` | `--mode preopen` (approximated from pre-open snapshots) | Added `--mode bhavcopy` (real NSE EOD CSV) |
| Pre-open snapshots | `run_preopen.py --test` (with mock fallback) | `load_preopen_live.py --force` (live NSE API) |
| Config | No data mode switch | `DATA_MODE=simulated` or `DATA_MODE=live` |
| New file | — | `app/collectors/nse_bhavcopy.py` |
| New script | — | `scripts/load_preopen_live.py` |

All previous modes (`seed`, `preopen`, mock pre-open) still work. Milestone 5
only **adds** a live path — nothing is removed.

---

### Bhavcopy data source

**URL:** `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv`

For example, for the session of 24 June 2026:
```
https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_24062026.csv
```

- Format: plain CSV, no login required, available after ~6 pm on the session date.
- Fallback: if the plain CSV is unavailable, the collector tries the zipped new-format
  bhavcopy at `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`.
- Columns used: `SYMBOL`, `HIGH_PRICE`, `LOW_PRICE`, `CLOSE_PRICE` (EQ series only).

---

### Pre-open live script

`scripts/load_preopen_live.py` fetches the same NSE pre-open API endpoint used
by the existing collector, but adds:
- Explicit `--date` parameter.
- `--force` flag to run outside market hours (for dry-runs).
- `--session` flag to run continuous polling for the full 9:00–9:15 am window.

---

### DATA_MODE configuration

Set `DATA_MODE` in your `.env` file or as an environment variable before running scripts:

```bash
# Default — uses seed/approximation flows, no internet required
DATA_MODE=simulated

# Live — uses real NSE bhavcopy and live pre-open API
DATA_MODE=live
```

The `DATA_MODE` variable only changes the **default** mode. You can always pass
`--mode` explicitly to override:

```bash
# This always uses bhavcopy regardless of DATA_MODE
python scripts/load_daily_levels.py --date 2026-06-25 --mode bhavcopy
```

---

### Live morning run — step-by-step

Use this workflow on a real trading day (e.g. Friday 2026-06-26):

```bash
export DATA_MODE=live

# Step 1: Load yesterday's EOD bhavcopy (2026-06-25) as "previous day" levels
# Run this after ~6 pm on 2026-06-25, or any time on 2026-06-26 morning
python scripts/load_daily_levels.py --date 2026-06-25 --mode bhavcopy

# Step 2: Fetch today's pre-open data (run during 9:00–9:15 am IST)
python scripts/load_preopen_live.py --date 2026-06-26

# --- OR if you want to test outside market hours ---
python scripts/load_preopen_live.py --date 2026-06-26 --force

# Step 3: Run scoring for today
python scripts/run_scoring.py --date 2026-06-26

# Step 4: Classify events (seed events for now)
python scripts/classify_events.py --date 2026-06-26

# Step 5: Generate and save the morning brief
python scripts/run_morning_brief.py --date 2026-06-26 --save
```

---

### Simulated / offline run (tests and offline use)

```bash
export DATA_MODE=simulated

# Load seed levels (no network)
python scripts/load_daily_levels.py --date 2026-06-25 --mode seed

# Fetch pre-open using mock data
python scripts/run_preopen.py --test

# Run scoring
python scripts/run_scoring.py --date 2026-06-26
```

---

### Manual verification checklist (Milestone 5)

After setting `DATA_MODE=live`, run these checks manually on your machine:

1. **Bhavcopy download check**

   ```bash
   python scripts/load_daily_levels.py --date 2026-06-24 --mode bhavcopy
   ```
   Expected: see `Rows written: N` (N > 0). Source = `NSE_BHAVCOPY`.

   Verify in SQLite:
   ```sql
   SELECT symbol, prev_high, prev_low, prev_close, source
   FROM daily_levels
   WHERE trade_date = '2026-06-24'
   LIMIT 10;
   ```

2. **Live pre-open check** (run during 9:00–9:15 am IST on a trading day)

   ```bash
   python scripts/load_preopen_live.py --date 2026-06-26
   ```
   Or outside market hours:
   ```bash
   python scripts/load_preopen_live.py --date 2026-06-26 --force
   ```
   Expected: see `Rows saved: N`. Source = `live_nse`.

3. **Full pipeline check**

   ```bash
   python scripts/load_daily_levels.py --date 2026-06-24 --mode bhavcopy
   python scripts/run_preopen.py --test
   python scripts/run_scoring.py --date 2026-06-25
   python scripts/run_morning_brief.py --date 2026-06-25
   ```

4. **Test suite check**

   ```bash
   pytest tests/ -v
   ```
   All tests should pass, including `test_bhavcopy.py` and `test_preopen_live.py`.

---

### Updated project tree (Milestone 5)

```
scripts/
  load_daily_levels.py  ← Updated: added --mode bhavcopy, DATA_MODE support
  load_preopen_live.py  ← NEW: live pre-open fetcher

app/
  collectors/
    nse_bhavcopy.py     ← NEW: bhavcopy HTTP client and CSV parser

tests/
  test_bhavcopy.py      ← NEW: offline unit tests for bhavcopy collector
  test_preopen_live.py  ← NEW: offline unit tests for live pre-open script

.env.example            ← Updated: added DATA_MODE variable
app/config.py           ← Updated: added data_mode setting
README.md               ← Updated: Milestone 5 live run docs
```
