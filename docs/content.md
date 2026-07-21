# Content — DataF1 Backend

API has no marketing pages. Product copy is owned by the Flutter app / landing (if any).

## Product one-liner

DataF1 turns raw Formula 1 telemetry into visual graphs and plain-English insights fans can understand.

## AI summary voice (Groq prompts)

- Fan-friendly, no unexplained jargon
- 1–3 sentences max
- Specific numbers when helpful
- Do not start with the driver’s name
- No bullet points in generated summary text

## Ops strings

| Endpoint | Example message |
|----------|-----------------|
| `/warmup` | Pre-cache started / already running |
| Health redis | `ok` \| `unavailable` |
| Health precache | `idle` \| `running` \| `completed` \| `failed` |

## SEO / web

N/A for pure API. Client app store / web copy lives in Flutter `docs/content.md`.
