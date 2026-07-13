import json
import logging
import os
import time
from typing import Optional

import fastf1
import numpy as np

from app.concurrency import run_sync, single_flight
from app.config import settings
from app.redis_client import get_redis
from app.schemas.results import DriverResult, RaceResultsResponse

logger = logging.getLogger(__name__)

os.makedirs(settings.FASTF1_CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(settings.FASTF1_CACHE_DIR)

RESULTS_TTL = 60 * 60 * 24  # 24 hours


def _safe_int(value) -> Optional[int]:
    try:
        f = float(value)
        if np.isnan(f):
            return None
        return int(f)
    except (TypeError, ValueError):
        return None


def _safe_float(value) -> float:
    try:
        f = float(value)
        return 0.0 if np.isnan(f) else f
    except (TypeError, ValueError):
        return 0.0


def _format_gap(value) -> Optional[str]:
    """Format gap to leader — e.g. '+5.234s' or '+1 Lap'."""
    try:
        if value is None:
            return None
        s = str(value).strip()
        if not s or s.lower() in ("nan", "none", ""):
            return None
        if "lap" in s.lower():
            return s
        if hasattr(value, "total_seconds"):
            secs = value.total_seconds()
            if secs <= 0:
                return None
            return f"+{secs:.3f}s"
        return None
    except Exception:
        return None


def _load_results_session(year: int, round_number: int, session_key: str):
    session = fastf1.get_session(year, round_number, session_key)
    session.load(telemetry=False, weather=False, messages=False, laps=False)
    return session


def _load_fastest_lap_driver(year: int, round_number: int, session_key: str) -> Optional[str]:
    session_laps = fastf1.get_session(year, round_number, session_key)
    session_laps.load(telemetry=False, weather=False, messages=False, laps=True)
    fl = session_laps.laps.pick_fastest()
    return str(fl.get("Driver", ""))


async def get_results(
    year: int,
    round_number: int,
    session_key: str = "R",
) -> RaceResultsResponse:
    """
    Fetch race/qualifying results for a round.
    session_key: 'R' = Race, 'Q' = Qualifying, 'SQ' = Sprint Qualifying
    """
    cache_key = f"results:{year}:{round_number}:{session_key}"
    redis = await get_redis()

    cached = await redis.get(cache_key)
    if cached:
        logger.info(f"[Cache HIT] results_service.get_results for key: {cache_key}")
        return RaceResultsResponse(**json.loads(cached))

    async def _fill() -> RaceResultsResponse:
        redis_inner = await get_redis()
        again = await redis_inner.get(cache_key)
        if again:
            logger.info(
                f"[Cache HIT] results_service.get_results (post-flight) for key: {cache_key}"
            )
            return RaceResultsResponse(**json.loads(again))

        logger.info(
            f"[Cache MISS] results_service.get_results for key: {cache_key}. Fetching from FastF1..."
        )
        start_time = time.perf_counter()

        load_start = time.perf_counter()
        session = await run_sync(_load_results_session, year, round_number, session_key)
        load_duration = time.perf_counter() - load_start
        logger.info(
            f"[FastF1] session.load (telemetry=False) for "
            f"{year} R{round_number} {session_key} completed in {load_duration:.4f}s"
        )

        results_df = session.results
        event = session.event

        race_name = str(event.get("EventName", f"Round {round_number}"))
        circuit = str(event.get("Location", ""))
        date = str(event.get("EventDate", ""))[:10]

        fastest_lap_driver = None
        if session_key == "R":
            fl_start = time.perf_counter()
            try:
                fastest_lap_driver = await run_sync(
                    _load_fastest_lap_driver, year, round_number, session_key
                )
                fl_duration = time.perf_counter() - fl_start
                logger.info(
                    f"[FastF1] get_results fastest lap loaded in {fl_duration:.4f}s"
                )
            except Exception as e:
                fl_duration = time.perf_counter() - fl_start
                logger.warning(
                    f"Failed to fetch fastest lap after {fl_duration:.4f}s: {e}"
                )

        parse_start = time.perf_counter()
        driver_results: list[DriverResult] = []

        for _, row in results_df.iterrows():
            position = _safe_int(row.get("Position"))
            code = str(row.get("Abbreviation", "???"))
            full_name = str(row.get("FullName", code))
            team = str(row.get("TeamName", "Unknown"))
            grid = _safe_int(row.get("GridPosition"))
            points = _safe_float(row.get("Points"))
            status = str(row.get("Status", "Unknown"))

            gap = None
            if session_key == "R" and position and position > 1:
                gap = _format_gap(row.get("Time"))

            driver_results.append(
                DriverResult(
                    position=position,
                    driver_code=code,
                    driver_full_name=full_name,
                    team=team,
                    grid_position=grid,
                    points=points,
                    status=status,
                    fastest_lap=(code == fastest_lap_driver),
                    gap_to_leader=gap,
                )
            )

        driver_results.sort(key=lambda x: (x.position is None, x.position or 99))

        response = RaceResultsResponse(
            year=year,
            round=round_number,
            race_name=race_name,
            session=session_key,
            circuit=circuit,
            date=date,
            results=driver_results,
            total_drivers=len(driver_results),
        )

        parse_duration = time.perf_counter() - parse_start
        logger.info(
            f"[Process] Processed {len(driver_results)} driver results in {parse_duration:.4f}s"
        )

        try:
            await redis_inner.setex(
                cache_key, RESULTS_TTL, json.dumps(response.model_dump())
            )
        except Exception as e:
            logger.warning(f"Failed to cache results: {e}")

        total_duration = time.perf_counter() - start_time
        logger.info(f"[Service] get_results completed in {total_duration:.4f}s")
        return response

    return await single_flight(cache_key, _fill)
