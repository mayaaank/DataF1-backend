import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.redis_client import get_redis, close_redis

logger = logging.getLogger(__name__)

# Idempotent warmup: only one pre-cache task at a time
_precache_task: asyncio.Task | None = None
_precache_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("═══ [Startup] DataF1 lifespan starting ═══")
    start_time = time.perf_counter()

    os.makedirs(settings.FASTF1_CACHE_DIR, exist_ok=True)
    logger.info(f"[Startup] Cache directory verified at {settings.FASTF1_CACHE_DIR}")

    logger.info("[Startup] Connecting to Redis...")
    await get_redis()
    logger.info("[Startup] Redis client connected successfully")

    startup_duration = time.perf_counter() - start_time
    logger.info(f"═══ [Startup] DataF1 backend fully ready in {startup_duration:.4f}s ═══")

    # Fire-and-forget pre-cache — never blocks startup
    await _start_precache_if_idle()

    yield

    # Shutdown
    logger.info("═══ [Shutdown] Closing Redis connection... ═══")
    await close_redis()
    logger.info("═══ [Shutdown] Lifespan cleanup complete ═══")


async def _start_precache_if_idle() -> bool:
    """Start background pre-cache if none is running. Returns True if started."""
    global _precache_task
    async with _precache_lock:
        if _precache_task is not None and not _precache_task.done():
            logger.info("[Pre-cache] Already running — skip duplicate start")
            return False
        _precache_task = asyncio.create_task(_run_precache())
        return True


async def _run_precache():
    """Background pre-cache task — runs after server is fully started."""
    await asyncio.sleep(5)
    precache_start = time.perf_counter()
    logger.info("═══ [Pre-cache] Background pre-cache job starting ═══")
    try:
        from app.services.precache_service import run_startup_precache

        await run_startup_precache()
        duration = time.perf_counter() - precache_start
        logger.info(
            f"═══ [Pre-cache] Background pre-cache job completed successfully "
            f"in {duration:.4f}s ═══"
        )
    except Exception as e:
        duration = time.perf_counter() - precache_start
        logger.error(
            f"═══ [Pre-cache] Background pre-cache job failed after {duration:.4f}s: {e} ═══"
        )


def _precache_status() -> str:
    if _precache_task is None:
        return "idle"
    if _precache_task.done():
        if _precache_task.cancelled():
            return "cancelled"
        if _precache_task.exception() is not None:
            return "failed"
        return "completed"
    return "running"


app = FastAPI(
    title="DataF1 API",
    description="Formula 1 telemetry interpretation and insight system",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_request_timing(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start_time
    logger.info(
        f"[Network/API] {request.method} {request.url.path} "
        f"completed in {duration:.4f}s with status {response.status_code}"
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["health"])
async def health_check():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/health", tags=["health"])
async def health_detail():
    """Liveness/readiness — keep cheap (no FastF1)."""
    redis = await get_redis()
    try:
        await redis.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "unavailable"
    return {
        "status": "ok",
        "redis": redis_status,
        "precache": _precache_status(),
    }


@app.get("/warmup", tags=["health"])
async def warmup():
    """
    Application preloading — starts bounded pre-cache in background.
    Idempotent: concurrent calls do not start a second full run.
    """
    started = await _start_precache_if_idle()
    return {
        "status": "warming" if started or _precache_status() == "running" else "idle",
        "started": started,
        "precache": _precache_status(),
        "message": (
            "Pre-cache started in background"
            if started
            else "Pre-cache already running or finished this process"
        ),
    }


@app.get("/cache-status", tags=["health"])
async def cache_status():
    """Show sample of Redis cache keys without blocking KEYS *."""
    redis = await get_redis()
    prefixes = ("telemetry:", "races:", "sessions:", "drivers:", "results:", "comparison:")
    counts = {p.rstrip(":"): 0 for p in prefixes}
    samples: dict[str, list[str]] = {p.rstrip(":"): [] for p in prefixes}
    total = 0

    # Prefer SCAN if available on the client wrapper
    try:
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match="*", count=200)
            for raw in keys:
                key = raw.decode() if isinstance(raw, bytes) else str(raw)
                total += 1
                for p in prefixes:
                    name = p.rstrip(":")
                    if key.startswith(p):
                        counts[name] += 1
                        if len(samples[name]) < 20:
                            samples[name].append(key)
                        break
            if cursor == 0:
                break
    except Exception as e:
        logger.warning(f"cache-status scan failed: {e}")
        return {"total_keys": 0, "error": str(e)}

    return {
        "total_keys": total,
        "counts": counts,
        "telemetry_cached": counts.get("telemetry", 0),
        "races_cached": counts.get("races", 0),
        "results_cached": counts.get("results", 0),
        "telemetry_keys": sorted(samples.get("telemetry", []))[:20],
    }


from app.routers import auth, races, telemetry, results  # noqa: E402

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(races.router, prefix="/races", tags=["races"])
app.include_router(telemetry.router, prefix="/telemetry", tags=["telemetry"])
app.include_router(results.router, prefix="/races", tags=["results"])
