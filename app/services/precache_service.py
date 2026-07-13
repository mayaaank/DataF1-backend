import asyncio
import logging
from datetime import datetime, timezone

import fastf1

from app.concurrency import run_sync
from app.redis_client import get_redis
from app.services import races_service, telemetry_service

logger = logging.getLogger(__name__)

# Bounded free-tier policy: metadata always; telemetry capped.
PRIORITY_DRIVERS = ["VER", "NOR", "LEC", "HAM", "PIA", "RUS", "SAI", "PIA","ANT"]
PRIORITY_METRICS = ["throttle","speed", "lap_time"]
LAST_N_RACES = 2


async def get_completed_rounds(year: int) -> list[int]:
    """Get the last N completed race rounds."""
    try:
        schedule = await run_sync(
            fastf1.get_event_schedule, year, include_testing=False
        )
        today = datetime.now(timezone.utc)
        past = schedule[schedule["EventDate"] < today]
        if past.empty:
            return [1]
        rounds = list(past["RoundNumber"].astype(int))
        return rounds[-LAST_N_RACES:]
    except Exception as e:
        logger.warning(f"Could not get completed rounds: {e}")
        return [1]


async def precache_race_schedule(year: int) -> None:
    """Pre-cache full season schedule — never changes."""
    try:
        logger.info(f"Pre-caching {year} schedule...")
        await races_service.get_races(year)
        logger.info("✓ Schedule cached")
    except Exception as e:
        logger.error(f"Schedule cache failed: {e}")


async def precache_all_sessions_and_drivers(year: int, rounds: list[int]) -> None:
    """Pre-cache sessions + drivers for given rounds."""
    for round_number in rounds:
        try:
            logger.info(f"Pre-caching sessions+drivers R{round_number}...")
            sessions = await races_service.get_sessions(year, round_number)
            for session in sessions:
                try:
                    await races_service.get_drivers(
                        year, round_number, session.key
                    )
                    logger.info(f"  ✓ {session.name} drivers")
                except Exception as e:
                    logger.warning(f"  ✗ {session.name}: {e}")
        except Exception as e:
            logger.error(f"Sessions cache failed R{round_number}: {e}")


async def precache_telemetry_batch(year: int, rounds: list[int]) -> None:
    """Pre-cache Race telemetry for priority drivers × metrics (capped)."""
    redis = await get_redis()

    for round_number in rounds:
        logger.info(f"Pre-caching telemetry R{round_number}...")
        for driver in PRIORITY_DRIVERS:
            for metric in PRIORITY_METRICS:
                cache_key = (
                    f"telemetry:{year}:{round_number}:R:{driver}:{metric}:0"
                )
                try:
                    if await redis.get(cache_key):
                        logger.info(f"  ✓ {driver} {metric} (already cached)")
                        continue

                    logger.info(f"  Fetching {driver} {metric}...")
                    await telemetry_service.get_telemetry(
                        year=year,
                        round_number=round_number,
                        session_key="R",
                        driver_code=driver,
                        metric=metric,
                        lap_number=0,
                    )
                    logger.info(f"  ✓ {driver} {metric}")
                    await asyncio.sleep(1)  # be nice to FastF1 / free CPU
                except Exception as e:
                    logger.warning(f"  ✗ {driver} {metric}: {e}")


async def run_startup_precache() -> None:
    """Full pre-cache — runs in background on startup (bounded for free tier)."""
    year = datetime.now().year
    logger.info("═══ DataF1 startup pre-cache starting ═══")

    rounds = await get_completed_rounds(year)
    logger.info(f"Pre-caching rounds: {rounds}")

    # 1. Race schedule (fast)
    await precache_race_schedule(year)

    # 2. Sessions + drivers for last N races + next race
    all_rounds_to_cache = rounds.copy()
    if rounds:
        next_round = max(rounds) + 1
        all_rounds_to_cache.append(next_round)
    await precache_all_sessions_and_drivers(year, all_rounds_to_cache)

    # 3. Telemetry for completed races only (capped drivers × metrics)
    await precache_telemetry_batch(year, rounds)

    logger.info("═══ Pre-cache complete ═══")
