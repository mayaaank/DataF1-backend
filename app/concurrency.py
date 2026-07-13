"""Concurrency helpers: offload blocking FastF1 work and collapse stampede.

- run_sync: asyncio.to_thread wrapper for blocking CPU/IO (FastF1, sync HTTP).
- single_flight: only one in-flight compute per key; concurrent waiters share the Task.

In-process single-flight is sufficient when uvicorn workers=1 (Dockerfile / free tier).
With multiple workers each process has its own map — still reduces per-process stampede.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_inflight: dict[str, asyncio.Task] = {}
_inflight_lock = asyncio.Lock()


async def run_sync(fn: Callable[..., T], /, *args, **kwargs) -> T:
    """Run a blocking callable in a worker thread (keeps the event loop free)."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def single_flight(key: str, factory: Callable[[], Awaitable[T]]) -> T:
    """
    Coalesce concurrent callers for the same key onto one Task.

    factory must be a zero-arg async callable (e.g. lambda: _compute()).
    On success or failure, the in-flight entry is cleared so later calls retry.
    """
    async with _inflight_lock:
        existing = _inflight.get(key)
        if existing is not None and not existing.done():
            logger.info(f"[SingleFlight] join in-flight key={key}")
            task: asyncio.Task = existing
        else:
            logger.info(f"[SingleFlight] start compute key={key}")
            task = asyncio.create_task(factory())
            _inflight[key] = task

    try:
        return await task
    finally:
        async with _inflight_lock:
            current = _inflight.get(key)
            if current is task and task.done():
                _inflight.pop(key, None)
