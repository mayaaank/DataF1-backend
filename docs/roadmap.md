# Roadmap — DataF1 Backend

## Completed

| Milestone | Notes |
|-----------|--------|
| Auth | JWT register/login/refresh |
| Races API | Schedule, sessions, drivers, metrics |
| Telemetry + AI summary | Graphs + Groq |
| Results + comparison | Race results, driver compare |
| Redis caching | Response-level TTL cache |
| Startup pre-cache | Bounded background warm |
| `/warmup`, `/cache-status` | Ops endpoints |
| FastF1 off event loop | `run_sync` / `to_thread` |
| Single-flight miss path | Stampede protection |
| Free-tier precache cap | Fewer drivers/metrics/races |

## Current

- Verify deploy on branch `mayaaank/perf/fastf1-single-flight-warmup` (Supabase DNS / env).
- Align docs with Flutter Phase B (splash + local SWR).

## Upcoming

| Item | Priority |
|------|----------|
| Stable production `DATABASE_URL` + migrations | High |
| Optional keep-alive ping to `/health` | Medium |
| Race-open predictive prefetch (capped) | Medium |
| Multi-worker + Redis locks (only if scaling) | Low |
| Predictions module (product Block 5) | Product |

## Feature roadmap (product blocks)

| Block | Feature | Backend status |
|-------|---------|----------------|
| 0–1 | Scaffold + auth | Done |
| 2 | Races selection | Done |
| 3 | Telemetry + summary | Done |
| 4 | Results + comparison | Done |
| 5 | Predictions | Pending |
| 6 | Profile / polish | Partial |
