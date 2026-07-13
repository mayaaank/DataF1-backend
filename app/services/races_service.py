import json
import logging
import os
import time

import fastf1

from app.concurrency import run_sync, single_flight
from app.config import settings
from app.redis_client import get_redis
from app.schemas.races import (
    DriverResponse,
    MetricResponse,
    RaceResponse,
    SessionResponse,
)

logger = logging.getLogger(__name__)

# FastF1 cache setup
os.makedirs(settings.FASTF1_CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(settings.FASTF1_CACHE_DIR)

# Redis TTL
RACES_TTL = 60 * 60 * 24  # 24 hours — race calendar doesn't change
SESSIONS_TTL = 60 * 60 * 6  # 6 hours
DRIVERS_TTL = 60 * 60 * 6   # 6 hours

# Fixed metrics — these never change
AVAILABLE_METRICS: list[MetricResponse] = [
    MetricResponse(key="throttle", label="Throttle", unit="%"),
    MetricResponse(key="brake", label="Brake", unit="%"),
    MetricResponse(key="speed", label="Speed", unit="km/h"),
    MetricResponse(key="lap_time", label="Lap Time", unit="s"),
    MetricResponse(key="top_speed", label="Top Speed", unit="km/h"),
]


def _fetch_schedule(year: int):
    return fastf1.get_event_schedule(year, include_testing=False)


def _fetch_event(year: int, round_number: int):
    return fastf1.get_event(year, round_number)


def _load_drivers_session(year: int, round_number: int, session_key: str):
    session = fastf1.get_session(year, round_number, session_key)
    session.load(telemetry=False, weather=False, messages=False)
    return session


async def get_races(year: int) -> list[RaceResponse]:
    """Return all races for a given season, Redis-cached."""
    cache_key = f"races:{year}"
    redis = await get_redis()

    cached = await redis.get(cache_key)
    if cached:
        logger.info(f"[Cache HIT] races_service.get_races for key: {cache_key}")
        data = json.loads(cached)
        return [RaceResponse(**r) for r in data]

    async def _fill() -> list[RaceResponse]:
        redis_inner = await get_redis()
        again = await redis_inner.get(cache_key)
        if again:
            logger.info(f"[Cache HIT] races_service.get_races (post-flight) for key: {cache_key}")
            return [RaceResponse(**r) for r in json.loads(again)]

        logger.info(
            f"[Cache MISS] races_service.get_races for key: {cache_key}. Fetching from FastF1..."
        )
        start_time = time.perf_counter()
        try:
            schedule = await run_sync(_fetch_schedule, year)
            duration = time.perf_counter() - start_time
            logger.info(
                f"[FastF1] get_event_schedule for year {year} completed in {duration:.4f}s"
            )
            races: list[RaceResponse] = []

            for _, row in schedule.iterrows():
                try:
                    races.append(
                        RaceResponse(
                            round=int(row["RoundNumber"]),
                            name=str(row["EventName"]),
                            country=str(row["Country"]),
                            circuit=str(row["Location"]),
                            date=str(row["EventDate"].date()),
                        )
                    )
                except Exception as e:
                    logger.warning(f"Skipping race row: {e}")
                    continue

            await redis_inner.setex(
                cache_key,
                RACES_TTL,
                json.dumps([r.model_dump() for r in races]),
            )
            return races

        except Exception as e:
            duration = time.perf_counter() - start_time
            logger.error(
                f"[FastF1] get_event_schedule failed for year {year} after {duration:.4f}s: {e}"
            )
            raise

    return await single_flight(cache_key, _fill)


async def get_sessions(year: int, round_number: int) -> list[SessionResponse]:
    """Return available sessions for a race weekend, Redis-cached."""
    cache_key = f"sessions:{year}:{round_number}"
    redis = await get_redis()

    cached = await redis.get(cache_key)
    if cached:
        logger.info(f"[Cache HIT] races_service.get_sessions for key: {cache_key}")
        data = json.loads(cached)
        return [SessionResponse(**s) for s in data]

    async def _fill() -> list[SessionResponse]:
        redis_inner = await get_redis()
        again = await redis_inner.get(cache_key)
        if again:
            logger.info(
                f"[Cache HIT] races_service.get_sessions (post-flight) for key: {cache_key}"
            )
            return [SessionResponse(**s) for s in json.loads(again)]

        logger.info(
            f"[Cache MISS] races_service.get_sessions for key: {cache_key}. Fetching from FastF1..."
        )
        start_time = time.perf_counter()
        try:
            event = await run_sync(_fetch_event, year, round_number)
            duration_event = time.perf_counter() - start_time
            logger.info(
                f"[FastF1] get_event for {year} R{round_number} completed in {duration_event:.4f}s"
            )

            sessions: list[SessionResponse] = []

            session_map = {
                "Practice 1": "FP1",
                "Practice 2": "FP2",
                "Practice 3": "FP3",
                "Sprint Shootout": "SS",
                "Sprint": "S",
                "Qualifying": "Q",
                "Race": "R",
            }

            for name, key in session_map.items():
                try:
                    session_date = getattr(event, name.replace(" ", ""), None)
                    if session_date is not None:
                        sessions.append(SessionResponse(key=key, name=name))
                except Exception:
                    continue

            if not sessions:
                sessions = [
                    SessionResponse(key="FP1", name="Practice 1"),
                    SessionResponse(key="FP2", name="Practice 2"),
                    SessionResponse(key="FP3", name="Practice 3"),
                    SessionResponse(key="Q", name="Qualifying"),
                    SessionResponse(key="R", name="Race"),
                ]

            await redis_inner.setex(
                cache_key,
                SESSIONS_TTL,
                json.dumps([s.model_dump() for s in sessions]),
            )
            return sessions

        except Exception as e:
            duration = time.perf_counter() - start_time
            logger.error(
                f"[FastF1] get_sessions failed for {year} round {round_number} "
                f"after {duration:.4f}s: {e}"
            )
            raise

    return await single_flight(cache_key, _fill)


async def get_drivers(
    year: int, round_number: int, session_key: str
) -> list[DriverResponse]:
    """Return drivers who participated in a session, Redis-cached."""
    cache_key = f"drivers:{year}:{round_number}:{session_key}"
    redis = await get_redis()

    cached = await redis.get(cache_key)
    if cached:
        logger.info(f"[Cache HIT] races_service.get_drivers for key: {cache_key}")
        data = json.loads(cached)
        return [DriverResponse(**d) for d in data]

    async def _fill() -> list[DriverResponse]:
        redis_inner = await get_redis()
        again = await redis_inner.get(cache_key)
        if again:
            logger.info(
                f"[Cache HIT] races_service.get_drivers (post-flight) for key: {cache_key}"
            )
            return [DriverResponse(**d) for d in json.loads(again)]

        logger.info(
            f"[Cache MISS] races_service.get_drivers for key: {cache_key}. Fetching from FastF1..."
        )
        start_time = time.perf_counter()
        try:
            load_start = time.perf_counter()
            session = await run_sync(
                _load_drivers_session, year, round_number, session_key
            )
            load_duration = time.perf_counter() - load_start
            logger.info(
                f"[FastF1] session.load (telemetry=False) for "
                f"{year} R{round_number} {session_key} completed in {load_duration:.4f}s"
            )

            drivers: list[DriverResponse] = []
            results = session.results

            for _, row in results.iterrows():
                try:
                    drivers.append(
                        DriverResponse(
                            code=str(row.get("Abbreviation", "UNK")),
                            full_name=str(
                                row.get("FullName", row.get("Abbreviation", "Unknown"))
                            ),
                            team=str(row.get("TeamName", "Unknown")),
                            number=str(row.get("DriverNumber", "")),
                        )
                    )
                except Exception as e:
                    logger.warning(f"Skipping driver row: {e}")
                    continue

            await redis_inner.setex(
                cache_key,
                DRIVERS_TTL,
                json.dumps([d.model_dump() for d in drivers]),
            )
            return drivers

        except Exception as e:
            duration = time.perf_counter() - start_time
            logger.error(
                f"[FastF1] get_drivers failed for "
                f"{year} round {round_number} {session_key} after {duration:.4f}s: {e}"
            )
            raise

    return await single_flight(cache_key, _fill)


def get_metrics() -> list[MetricResponse]:
    """Return available telemetry metrics — static, no FastF1 call needed."""
    return AVAILABLE_METRICS
