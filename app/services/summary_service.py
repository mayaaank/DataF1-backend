import logging
import time
from typing import Optional

from groq import Groq

from app.concurrency import run_sync
from app.config import settings

logger = logging.getLogger(__name__)
MODEL = "llama-3.1-8b-instant"


def _call_groq(prompt: str) -> str:
    client = Groq(api_key=settings.GROQ_API_KEY)
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


def _build_prompt(driver, team, metric, session, race, year, data_points, fastest_lap):
    values = [dp["y"] for dp in data_points if dp.get("y") is not None]
    if not values:
        return ""
    avg_val = sum(values) / len(values)
    max_val = max(values)
    min_val = min(values)
    total_laps = len(set(dp["x"] for dp in data_points))
    metric_context = {
        "throttle": f"throttle input (%). Average: {avg_val:.1f}%, Max: {max_val:.1f}%, Min: {min_val:.1f}%",
        "brake": f"brake input (%). Average: {avg_val:.1f}%, Max: {max_val:.1f}%, Min: {min_val:.1f}%",
        "speed": f"speed (km/h). Average: {avg_val:.1f} km/h, Top: {max_val:.1f} km/h",
        "lap_time": f"lap times (seconds). Average: {avg_val:.3f}s, Fastest: {min_val:.3f}s, Slowest: {max_val:.3f}s",
        "top_speed": f"top speed per lap (km/h). Average: {avg_val:.1f} km/h, Highest: {max_val:.1f} km/h",
    }.get(metric, f"{metric}. Average: {avg_val:.2f}, Max: {max_val:.2f}, Min: {min_val:.2f}")
    fastest_info = f" Fastest lap: {fastest_lap:.3f}s." if fastest_lap else ""
    return f"""You are a Formula 1 data analyst writing insight summaries for a telemetry app.
Your summaries are read by F1 fans who may not be technical experts.

Driver: {driver} ({team})
Race: {race} {year} — {session}
Metric: {metric_context}
Total laps analysed: {total_laps}{fastest_info}

Write a 1-3 sentence insight summary about this driver's {metric} data.
Rules:
- No jargon or acronyms without explanation
- Highlight the key trend (consistent, aggressive, variable, improving, declining)
- Note one specific observation
- Write as if explaining to an interested fan, not an engineer
- Be specific with numbers where helpful
- Do NOT start with the driver's name
- Do NOT use bullet points
- Maximum 3 sentences"""


async def generate_summary(
    driver: str,
    team: str,
    metric: str,
    session: str,
    race: str,
    year: int,
    data_points: list[dict],
    fastest_lap: Optional[float],
) -> str:
    if not settings.GROQ_API_KEY:
        return f"Telemetry data loaded for {driver}. Summary generation is temporarily unavailable."
    prompt = _build_prompt(driver, team, metric, session, race, year, data_points, fastest_lap)
    if not prompt:
        return f"Telemetry data loaded for {driver}. Summary generation is temporarily unavailable."
    
    start_time = time.perf_counter()
    logger.info(f"[Groq] Requesting completion for {driver} ({metric}) using model {MODEL}...")
    try:
        summary = await run_sync(_call_groq, prompt)
        duration = time.perf_counter() - start_time
        logger.info(
            f"[Groq] Summary generated for {driver} {metric} completed in "
            f"{duration:.4f}s ({len(summary)} chars)"
        )
        return summary
    except Exception as e:
        duration = time.perf_counter() - start_time
        logger.error(f"[Groq] API failed for {driver} {metric} after {duration:.4f}s: {e}")
        return f"Telemetry data loaded for {driver}. Summary generation is temporarily unavailable."

