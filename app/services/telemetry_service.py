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
from app.schemas.telemetry import (
    ComparisonDriver,
    ComparisonResponse,
    DataPoint,
    LapInfo,
    TelemetryResponse,
)
from app.services import summary_service

logger = logging.getLogger(__name__)

os.makedirs(settings.FASTF1_CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(settings.FASTF1_CACHE_DIR)

TELEMETRY_TTL = 60 * 60 * 12
TARGET_POINTS = 300

METRIC_META = {
    "throttle":  {"label": "Throttle",  "unit": "%",    "channel": "Throttle"},
    "brake":     {"label": "Brake",     "unit": "%",    "channel": "Brake"},
    "speed":     {"label": "Speed",     "unit": "km/h", "channel": "Speed"},
    "lap_time":  {"label": "Lap Time",  "unit": "s",    "channel": None},
    "top_speed": {"label": "Top Speed", "unit": "km/h", "channel": None},
}


def _safe_float(value) -> Optional[float]:
    try:
        f = float(value)
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def _smooth_and_resample(
    x_raw: np.ndarray,
    y_raw: np.ndarray,
    n_points: int,
    channel: str,
) -> list[DataPoint]:
    """Resample to even grid, smooth, keep x in real metres."""
    mask = np.isfinite(x_raw) & np.isfinite(y_raw)
    x_raw, y_raw = x_raw[mask], y_raw[mask]
    if len(x_raw) < 2:
        return []
    sort_idx = np.argsort(x_raw)
    x_raw, y_raw = x_raw[sort_idx], y_raw[sort_idx]
    x_even = np.linspace(x_raw[0], x_raw[-1], n_points)
    y_interp = np.interp(x_even, x_raw, y_raw)
    window = 3 if channel == "Brake" else (7 if channel == "Throttle" else 5)
    kernel = np.ones(window) / window
    y_smooth = np.convolve(y_interp, kernel, mode="same")
    if channel in ("Throttle", "Brake"):
        y_smooth = np.clip(y_smooth, 0, 100)
    return [
        DataPoint(x=round(float(x_even[i]), 1), y=round(float(y_smooth[i]), 2))
        for i in range(n_points)
    ]


def _extract_lap_telemetry(lap, channel: str) -> list[DataPoint]:
    """Extract smoothed distance-based telemetry for one lap."""
    try:
        tel = lap.get_telemetry()
        if tel is None or len(tel) == 0 or channel not in tel.columns:
            return []
        x_raw = tel["Distance"].values.astype(float)
        y_raw = tel[channel].values.astype(float)
        if channel == "Brake":
            y_raw = (y_raw > 0).astype(float) * 100.0
        return _smooth_and_resample(x_raw, y_raw, TARGET_POINTS, channel)
    except Exception as e:
        logger.warning(f"Lap telemetry extraction failed: {e}")
        return []


def _build_lap_infos(laps, fastest_lap_num: Optional[int]) -> list[LapInfo]:
    """Build lap list for the picker sheet."""
    infos = []
    for _, lap in laps.iterrows():
        ln = _safe_float(lap.get("LapNumber"))
        if ln is None:
            continue
        ln_int = int(ln)
        lt = lap.get("LapTime")
        seconds = None
        try:
            if hasattr(lt, "total_seconds"):
                s = lt.total_seconds()
                if 60 < s < 300:
                    seconds = round(s, 3)
        except Exception:
            pass
        infos.append(LapInfo(
            lap_number=ln_int,
            lap_time=seconds,
            is_fastest=(ln_int == fastest_lap_num),
        ))
    return sorted(infos, key=lambda x: x.lap_number)


def _load_telemetry_session(year: int, round_number: int, session_key: str):
    session = fastf1.get_session(year, round_number, session_key)
    session.load(
        telemetry=True, weather=False, messages=False, laps=True, livedata=None
    )
    return session


def _race_name_from_schedule(year: int, round_number: int) -> str:
    schedule = fastf1.get_event_schedule(year, include_testing=False)
    event = schedule[schedule["RoundNumber"] == round_number]
    if not event.empty:
        return str(event.iloc[0]["EventName"])
    return f"Round {round_number}"


async def _get_race_name(year: int, round_number: int) -> str:
    try:
        return await run_sync(_race_name_from_schedule, year, round_number)
    except Exception:
        return f"Round {round_number}"


async def get_telemetry(
    year: int,
    round_number: int,
    session_key: str,
    driver_code: str,
    metric: str,
    lap_number: int = 0,
) -> TelemetryResponse:
    """
    Fetch telemetry for a driver + metric.
    lap_number=0 → fastest lap. Any other value → that specific lap.
    Cache key includes lap_number so each lap cached separately.
    """
    cache_key = (
        f"telemetry:{year}:{round_number}:{session_key}"
        f":{driver_code}:{metric}:{lap_number}"
    )
    redis = await get_redis()

    cached = await redis.get(cache_key)
    if cached:
        logger.info(f"[Cache HIT] telemetry_service.get_telemetry for key: {cache_key}")
        return TelemetryResponse(**json.loads(cached))

    async def _fill() -> TelemetryResponse:
        redis_inner = await get_redis()
        again = await redis_inner.get(cache_key)
        if again:
            logger.info(
                f"[Cache HIT] telemetry_service.get_telemetry (post-flight) for key: {cache_key}"
            )
            return TelemetryResponse(**json.loads(again))

        logger.info(
            f"[Cache MISS] telemetry_service.get_telemetry for key: {cache_key}. "
            f"Fetching from FastF1..."
        )
        start_time = time.perf_counter()

        if metric not in METRIC_META:
            raise ValueError(f"Unknown metric: {metric}")

        meta = METRIC_META[metric]

        load_start = time.perf_counter()
        session = await run_sync(
            _load_telemetry_session, year, round_number, session_key
        )
        load_duration = time.perf_counter() - load_start
        logger.info(
            f"[FastF1] session.load (telemetry=True) for "
            f"{year} R{round_number} {session_key} completed in {load_duration:.4f}s"
        )

        results = session.results
        driver_row = results[results["Abbreviation"] == driver_code]
        driver_full_name = driver_code
        team = "Unknown"
        if not driver_row.empty:
            driver_full_name = str(driver_row.iloc[0].get("FullName", driver_code))
            team = str(driver_row.iloc[0].get("TeamName", "Unknown"))

        laps = session.laps.pick_drivers(driver_code)
        partial = False
        data_points: list[DataPoint] = []
        fastest_lap_time: Optional[float] = None
        total_laps = len(laps)
        selected_lap = lap_number

        fastest_lap_num = None
        try:
            fl = laps.pick_fastest()
            fastest_lap_num = int(fl.get("LapNumber"))
        except Exception:
            pass

        lap_infos = _build_lap_infos(laps, fastest_lap_num)

        parse_start = time.perf_counter()
        if metric == "lap_time":
            for _, lap in laps.iterrows():
                ln = _safe_float(lap.get("LapNumber"))
                lt = lap.get("LapTime")
                if ln is None:
                    continue
                try:
                    s = lt.total_seconds() if hasattr(lt, "total_seconds") else None
                    if s and 60 < s < 300:
                        data_points.append(DataPoint(x=ln, y=round(s, 3)))
                except Exception:
                    partial = True
            if data_points:
                fastest_lap_time = min(dp.y for dp in data_points)
            selected_lap = 0

        elif metric == "top_speed":
            for _, lap in laps.iterrows():
                ln = _safe_float(lap.get("LapNumber"))
                if ln is None:
                    continue
                try:
                    tel = lap.get_telemetry()
                    if tel is None or len(tel) == 0:
                        partial = True
                        continue
                    max_speed = _safe_float(tel["Speed"].dropna().max())
                    if max_speed and max_speed > 100:
                        data_points.append(DataPoint(x=ln, y=max_speed))
                except Exception:
                    partial = True
            selected_lap = 0

        else:
            channel = meta["channel"]
            try:
                if lap_number == 0:
                    target_lap = laps.pick_fastest()
                    selected_lap = fastest_lap_num or 0
                else:
                    mask = laps["LapNumber"] == lap_number
                    if not mask.any():
                        raise ValueError(f"Lap {lap_number} not found for {driver_code}")
                    target_lap = laps[mask].iloc[0]
                    selected_lap = lap_number

                data_points = _extract_lap_telemetry(target_lap, channel)
                lt = target_lap.get("LapTime")
                if lt is not None:
                    try:
                        fastest_lap_time = round(lt.total_seconds(), 3)
                    except Exception:
                        pass
                if not data_points:
                    partial = True
            except Exception as e:
                logger.warning(f"Lap telemetry failed for {driver_code}: {e}")
                partial = True

        parse_duration = time.perf_counter() - parse_start
        logger.info(f"[Process] Parsed and processed telemetry in {parse_duration:.4f}s")

        if not data_points:
            raise ValueError(f"No telemetry data available for {driver_code} {metric}")

        race_name = await _get_race_name(year, round_number)

        summary_start = time.perf_counter()
        summary = await summary_service.generate_summary(
            driver=driver_code,
            team=team,
            metric=metric,
            session=session_key,
            race=race_name,
            year=year,
            data_points=[{"x": dp.x, "y": dp.y} for dp in data_points],
            fastest_lap=fastest_lap_time,
        )
        summary_duration = time.perf_counter() - summary_start
        logger.info(
            f"[API/Service] Summary generation call completed in {summary_duration:.4f}s"
        )

        response = TelemetryResponse(
            year=year,
            round=round_number,
            session=session_key,
            driver=driver_code,
            driver_full_name=driver_full_name,
            team=team,
            metric=metric,
            metric_label=meta["label"],
            metric_unit=meta["unit"],
            data=data_points,
            total_laps=total_laps,
            fastest_lap=fastest_lap_time,
            selected_lap=selected_lap,
            laps=lap_infos,
            summary=summary,
            partial=partial,
        )

        try:
            await redis_inner.setex(
                cache_key, TELEMETRY_TTL, json.dumps(response.model_dump())
            )
        except Exception as e:
            logger.warning(f"Failed to cache: {e}")

        total_duration = time.perf_counter() - start_time
        logger.info(f"[Service] get_telemetry completed in {total_duration:.4f}s")
        return response

    return await single_flight(cache_key, _fill)


async def get_comparison(
    year: int,
    round_number: int,
    session_key: str,
    driver1_code: str,
    driver2_code: str,
    metric: str,
    lap_number: int = 0,
) -> ComparisonResponse:
    """Fetch telemetry for two drivers overlaid on the same graph."""
    if metric not in METRIC_META:
        raise ValueError(f"Unknown metric: {metric}")

    cache_key = (
        f"comparison:{year}:{round_number}:{session_key}"
        f":{driver1_code}:{driver2_code}:{metric}:{lap_number}"
    )
    redis = await get_redis()
    cached = await redis.get(cache_key)
    if cached:
        logger.info(f"[Cache HIT] telemetry_service.get_comparison for key: {cache_key}")
        return ComparisonResponse(**json.loads(cached))

    async def _fill() -> ComparisonResponse:
        redis_inner = await get_redis()
        again = await redis_inner.get(cache_key)
        if again:
            logger.info(
                f"[Cache HIT] telemetry_service.get_comparison (post-flight) for key: {cache_key}"
            )
            return ComparisonResponse(**json.loads(again))

        logger.info(
            f"[Cache MISS] telemetry_service.get_comparison for key: {cache_key}. "
            f"Fetching from FastF1..."
        )
        start_time = time.perf_counter()

        meta = METRIC_META[metric]
        load_start = time.perf_counter()
        session = await run_sync(
            _load_telemetry_session, year, round_number, session_key
        )
        load_duration = time.perf_counter() - load_start
        logger.info(
            f"[FastF1] session.load (telemetry=True) for "
            f"{year} R{round_number} {session_key} completed in {load_duration:.4f}s"
        )

        results = session.results

        def _driver_info(code: str):
            row = results[results["Abbreviation"] == code]
            if not row.empty:
                return (
                    str(row.iloc[0].get("FullName", code)),
                    str(row.iloc[0].get("TeamName", "Unknown")),
                )
            return code, "Unknown"

        def _build_driver(code: str) -> ComparisonDriver:
            driver_start = time.perf_counter()
            full_name, team = _driver_info(code)
            driver_laps = session.laps.pick_drivers(code)
            data_points: list[DataPoint] = []
            lap_time: Optional[float] = None
            sel_lap = lap_number

            if metric in ("lap_time", "top_speed"):
                for _, lap in driver_laps.iterrows():
                    ln = _safe_float(lap.get("LapNumber"))
                    if ln is None:
                        continue
                    if metric == "lap_time":
                        lt = lap.get("LapTime")
                        try:
                            s = lt.total_seconds() if hasattr(lt, "total_seconds") else None
                            if s and 60 < s < 300:
                                data_points.append(DataPoint(x=ln, y=round(s, 3)))
                        except Exception:
                            pass
                    else:
                        try:
                            tel = lap.get_telemetry()
                            if tel is not None and len(tel) > 0:
                                ms = _safe_float(tel["Speed"].dropna().max())
                                if ms and ms > 100:
                                    data_points.append(DataPoint(x=ln, y=ms))
                        except Exception:
                            pass
                if data_points and metric == "lap_time":
                    lap_time = min(dp.y for dp in data_points)
                sel_lap = 0
            else:
                channel = meta["channel"]
                try:
                    if lap_number == 0:
                        target = driver_laps.pick_fastest()
                        try:
                            sel_lap = int(target.get("LapNumber"))
                        except Exception:
                            sel_lap = 0
                    else:
                        mask = driver_laps["LapNumber"] == lap_number
                        target = (
                            driver_laps[mask].iloc[0]
                            if mask.any()
                            else driver_laps.pick_fastest()
                        )
                        sel_lap = lap_number
                    data_points = _extract_lap_telemetry(target, channel)
                    lt = target.get("LapTime")
                    if lt is not None:
                        try:
                            lap_time = round(lt.total_seconds(), 3)
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"Comparison lap failed for {code}: {e}")

            driver_duration = time.perf_counter() - driver_start
            logger.info(
                f"[Process] Processed comparison driver {code} in {driver_duration:.4f}s"
            )
            return ComparisonDriver(
                driver=code,
                driver_full_name=full_name,
                team=team,
                data=data_points,
                fastest_lap=lap_time,
                selected_lap=sel_lap,
            )

        d1 = _build_driver(driver1_code)
        d2 = _build_driver(driver2_code)
        race_name = await _get_race_name(year, round_number)

        sample = [{"x": dp.x, "y": dp.y} for dp in d1.data[:30] + d2.data[:30]]

        summary_start = time.perf_counter()
        summary = await summary_service.generate_summary(
            driver=f"{driver1_code} vs {driver2_code}",
            team=f"{d1.team} vs {d2.team}",
            metric=metric,
            session=session_key,
            race=race_name,
            year=year,
            data_points=sample,
            fastest_lap=d1.fastest_lap,
        )
        summary_duration = time.perf_counter() - summary_start
        logger.info(
            f"[API/Service] Comparison summary generation call completed in {summary_duration:.4f}s"
        )

        response = ComparisonResponse(
            year=year,
            round=round_number,
            session=session_key,
            metric=metric,
            metric_label=meta["label"],
            metric_unit=meta["unit"],
            driver1=d1,
            driver2=d2,
            summary=summary,
        )

        try:
            await redis_inner.setex(
                cache_key, TELEMETRY_TTL, json.dumps(response.model_dump())
            )
        except Exception as e:
            logger.warning(f"Failed to cache comparison: {e}")

        total_duration = time.perf_counter() - start_time
        logger.info(f"[Service] get_comparison completed in {total_duration:.4f}s")
        return response

    return await single_flight(cache_key, _fill)


# Note: position_data loading is disabled — not needed for telemetry channels
