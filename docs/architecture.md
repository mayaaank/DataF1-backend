# Architecture — DataF1 Backend

## System context

```
Flutter app (DataF1-flutter)
    │  HTTPS / Dio
    ▼
FastAPI (Render)
    ├── Redis          → cached API JSON (HIT < ~1s)
    ├── PostgreSQL     → users / auth
    ├── FastF1         → schedule, sessions, telemetry (MISS: slow)
    └── Groq           → plain-English graph summaries
```

## Request data flow

```
Request
  → Redis GET cache_key
  → HIT: return JSON
  → MISS: single_flight(key)
       → run_sync(FastF1 load/parse)
       → optional run_sync(Groq)
       → Redis SETEX
       → return
```

## Startup

1. Ensure FastF1 cache dir exists.
2. Connect Redis client (`TimedRedis`).
3. Start background pre-cache (idempotent; delayed ~5s).
4. HTTP middleware logs method, path, duration, status.

## Endpoints (summary)

| Path | Role |
|------|------|
| `GET /`, `GET /health` | Liveness; health includes redis + precache status |
| `GET /warmup` | Idempotent start of pre-cache |
| `GET /cache-status` | SCAN-based cache sample |
| `/auth/*` | Register, login, refresh, me |
| `/races/*` | Schedule, sessions, drivers, metrics |
| `/telemetry/*` | Graph + comparison |
| `/races/.../results` | Race results |

## Concurrency design

| Problem | Mitigation |
|---------|------------|
| Sync FastF1 blocks event loop | `asyncio.to_thread` via `run_sync` |
| Stampede on same key | In-process `single_flight` (assumes `workers=1`) |
| Redis down | TimedRedis soft-fails get→None; app falls back to FastF1 |

## Design decisions

- **workers=1** on free tier until Redis distributed locks exist.
- Pre-cache is capped to protect free CPU; not open-ended prediction.
- `/health` never runs FastF1; `/warmup` does application preloading.

## Deploy

- Platform: Render
- Env: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `GROQ_API_KEY`, `FASTF1_CACHE_DIR`
- Optional disk mount for FastF1 cache at `/tmp/fastf1`
