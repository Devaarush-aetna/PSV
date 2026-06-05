"""Exponential backoff retry decorator for async scraper functions."""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

_RETRYABLE_NAMES = {
    "timeout", "timeouterror", "navigation_error", "network_error",
    "targeterror", "connectionerror",
}


def _is_retryable(exc: Exception, retry_on: list[str]) -> bool:
    exc_name = type(exc).__name__.lower()
    exc_str = str(exc).lower()
    for pattern in retry_on:
        p = pattern.lower().replace("_", "")
        if p in exc_name or p in exc_str:
            return True
    return False


def with_retry(max_attempts: int = 3, backoff_ms: list[int] | None = None, retry_on: list[str] | None = None):
    """Decorator: retry an async function with exponential backoff."""
    _backoff = backoff_ms or [1000, 2000, 4000]
    _retry_on = retry_on or list(_RETRYABLE_NAMES)

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    if not _is_retryable(exc, _retry_on):
                        raise
                    last_exc = exc
                    delay = _backoff[min(attempt, len(_backoff) - 1)] / 1000.0
                    log.warning(
                        "Attempt %d/%d failed (%s: %s) — retrying in %.1fs",
                        attempt + 1, max_attempts, type(exc).__name__, exc, delay,
                    )
                    await asyncio.sleep(delay)
            raise RuntimeError(f"All {max_attempts} attempts failed") from last_exc
        return wrapper
    return decorator
