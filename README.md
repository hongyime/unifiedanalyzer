# UnifiedAnalyzer

Personal OSINT analyzer. Reads data from `unifiedcollector` PostgreSQL database, performs cross-platform identity resolution, builds unified timelines, and generates alerts.

## Quick Start

```bash
# Copy and edit env
cp .env.example .env

# Install Python deps
pip install -r requirements.txt

# Create the analyzer database (one-time)
# Connect to your Postgres and run: CREATE DATABASE unifiedanalyzer;

# Apply schema + run first analysis
python -m src.main schema
python -m src.main full

# Start the server (API + scheduler)
python -m src.main serve

# Frontend dev
cd frontend && npm install && npm run dev
```

## Commands

- `python -m src.main serve` — Start FastAPI server with scheduler
- `python -m src.main run` — One-shot incremental analysis
- `python -m src.main full` — Full identity re-resolution
- `python -m src.main schema` — Apply database schema

## Architecture

- **Entity Resolver** — Cross-platform identity linking (username match, WhatsApp JID phone, GitHub commit email, Strava real name, profile photo SHA256, name fuzzy match)
- **Timeline Builder** — Normalizes events from 10 platform tables into unified chronological feed
- **Alert Engine** — Silence gap detection (dynamic threshold), new-activity-after-silence, profile change
- **Scheduler** — 60-min incremental + nightly full resolution + on-demand trigger
- **API** — FastAPI on port 8001, endpoints for entities, timeline, alerts, runs, health
- **Frontend** — React + Vite + TypeScript dashboard
