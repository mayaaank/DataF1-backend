import logging
import time
import redis.asyncio as aioredis
from app.config import settings

logger = logging.getLogger(__name__)

class TimedRedis:
    """A proxy wrapper that logs the execution duration of all Redis commands."""
    def __init__(self, client: aioredis.Redis):
        self._client = client

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if callable(attr):
            async def async_wrapper(*args, **kwargs):
                start = time.perf_counter()
                try:
                    res = await attr(*args, **kwargs)
                    elapsed = time.perf_counter() - start
                    # Log the redis command and parameters (shortened for readability)
                    args_str = ", ".join(str(a)[:80] for a in args)
                    logger.info(f"[Redis] {name}({args_str}) completed in {elapsed:.4f}s")
                    return res
                except Exception as e:
                    elapsed = time.perf_counter() - start
                    args_str = ", ".join(str(a)[:80] for a in args)
                    logger.warning(
                        f"[Redis] {name}({args_str}) failed after {elapsed:.4f}s (suppressing connection error): {e}"
                    )
                    # If this is ping(), propagate the error so health check knows it's down
                    if name == "ping":
                        raise
                    # Return fallback values to allow service layer to fall back to FastF1/Groq
                    if name == "get":
                        return None
                    if name in ("set", "setex"):
                        return True
                    if name == "keys":
                        return []
                    return None
            return async_wrapper
        return attr


_redis_client: TimedRedis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        raw_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        _redis_client = TimedRedis(raw_client)
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        # Calls self._client.aclose() inside TimedRedis wrapper
        await _redis_client.aclose()
        _redis_client = None

