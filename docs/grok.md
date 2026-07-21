# DataF1 Backend — Grok / Agent Guide

Permanent project knowledge for coding agents. Prefer this file + other `docs/*` over chat history.

## Overview

DataF1 is an F1 **telemetry interpretation** API: FastF1 data → JSON graphs + AI summaries (Groq). Not a general stats encyclopedia.

## Tech stack

| Layer | Choice |
|-------|--------|
| API | FastAPI + Uvicorn |
| F1 data | FastF1 3.8.x |
| Cache | Redis (response JSON) + disk FastF1 cache |
| DB | PostgreSQL (Supabase) via SQLAlchemy async + Alembic |
| Auth | JWT (python-jose) + bcrypt |
| AI | Groq (`llama-3.1-8b-instant`) |
| Deploy | Render free/starter, `workers=1` |

## Layout

```
app/
  main.py           # lifespan, /health, /warmup, /cache-status, routers
  concurrency.py    # run_sync (to_thread) + single_flight
  redis_client.py   # TimedRedis proxy
  routers/          # auth, races, telemetry, results
  services/         # races, telemetry, results, summary, precache
  schemas/ models/
```

## Conventions

- Keep `/health` cheap (no FastF1). Preload via `/warmup` or lifespan pre-cache.
- Blocking FastF1/Groq must go through `app.concurrency.run_sync`.
- Expensive miss paths use `single_flight(cache_key, factory)` so concurrent requests share one compute.
- Cache keys: `races:`, `sessions:`, `drivers:`, `results:`, `telemetry:`, `comparison:`.
- Prefer SCAN over `KEYS *` for ops endpoints.
- Do not add `Co-Authored-By` trailers unless the human asks.

## Free-tier notes

- Render sleeps → first request wakes the process.
- Redis (external) can stay warm across app sleep.
- Pre-cache is **bounded** (last N races × priority drivers × metrics).
- Supabase must be unpaused; bad `DATABASE_URL` DNS fails deploy/migrations.

## Related docs

- `docs/architecture.md` — system design
- `docs/roadmap.md` — milestones
- `docs/PROJECT_CONTEXT.md` — current sprint
- `docs/TODO.md` — task board
- Sibling app: `DataF1-flutter`
