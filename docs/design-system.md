# Design system — DataF1 Backend

API project has no UI tokens. This file tracks **API / ops presentation conventions** that affect clients.

## Response conventions

- JSON bodies via Pydantic v2 schemas under `app/schemas/`.
- Errors: FastAPI `detail` string or structured HTTPException.
- Telemetry points: `{ "x": number, "y": number }` arrays.

## Log tags (ops / debugging)

| Tag | Meaning |
|-----|---------|
| `[Network/API]` | Per-request duration |
| `[Cache HIT]` / `[Cache MISS]` | Redis path |
| `[FastF1]` | Session load / schedule timing |
| `[SingleFlight]` | Coalesced compute |
| `[Groq]` | Summary generation |
| `[Pre-cache]` | Startup / warmup job |
| `[Redis]` | Timed command duration |

## Client-facing UX notes

Client design lives in **DataF1-flutter** `docs/design-system.md` (colors, typography, splash).
Backend only guarantees stable JSON contracts and cache behavior for SWR.
