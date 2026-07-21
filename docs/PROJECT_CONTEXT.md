# Project context — DataF1 Backend

Living sprint snapshot. Update often.

## Active focus

- Performance & free-tier resilience path (merged plan: A features + B safety order).
- Branch: `mayaaank/perf/fastf1-single-flight-warmup` (perf: to_thread, single-flight, bounded precache).
- Flutter Phase B shipped in sibling repo (splash wake + local SWR).

## Implementation status

| Area | Status |
|------|--------|
| Auth / races / telemetry / results | Production code present |
| Redis caching | Done |
| Observability logs + TimedRedis | Done |
| `concurrency.run_sync` + `single_flight` | Done |
| Bounded pre-cache + idempotent warmup | Done |
| Render workers=1 | Done in render.yaml |
| Deploy verify on free Render | Blocked earlier by Supabase DNS / paused project |

## Known issues

- Deploy can fail if Supabase host does not resolve (`could not translate host name db.*.supabase.co`). Fix env + unpause project; not caused by FastF1 perf code.
- Free Render still sleeps until traffic; Redis HIT can still be fast after wake.
- Multi-worker would weaken in-process single-flight (keep workers=1 or add Redis locks).

## Next priorities

1. Confirm Render deploy + smoke `/health`, `/warmup`, cache HIT/MISS.
2. Keep docs accurate as Flutter and backend evolve.
3. Only expand pre-cache after production logs prove headroom.
