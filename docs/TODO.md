# TODO — DataF1 Backend

## Completed

- [x] JWT auth
- [x] Races / sessions / drivers / metrics APIs
- [x] Telemetry + Groq summary
- [x] Results + comparison
- [x] Redis response cache
- [x] Startup pre-cache + `/warmup`
- [x] Request timing + cache/FastF1 logs
- [x] FastF1/Groq off event loop (`to_thread`)
- [x] Single-flight cache-miss coalescing
- [x] Bounded free-tier pre-cache
- [x] `/cache-status` via SCAN
- [x] Living docs under `docs/` (`grok.md`, architecture, roadmap, …)

## Pending

- [ ] Green Render deploy with valid Supabase `DATABASE_URL`
- [ ] Post-deploy smoke checklist (health, warmup, double-fetch HIT)
- [ ] Optional external uptime ping → `/health` every 10–14 min
- [ ] Predictions API (Block 5)
- [ ] Explicit cache invalidation paths (beyond TTL)

## Bugs

- [ ] Supabase DNS / paused project breaks build when migrations need DB

## Tech debt

- [ ] Align README FastF1/AI versions with current `requirements.txt` (Groq, FastF1 3.8)
- [ ] Redis locks if raising uvicorn workers > 1
- [ ] Avoid open-ended predictive precompute without caps

## Future

- Analytics-driven prefetch of popular driver/metric combos
- Always-on Render for demos
