"""
Utility helpers — rate limiting, retries, logging setup.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from functools import wraps
from typing import Any, Callable


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure structured logging for the application."""
    logger = logging.getLogger("dexbot")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)

    return logger


class AsyncRateLimiter:
    """
    Token-bucket rate limiter for async code.

    Usage:
        limiter = AsyncRateLimiter(max_calls=5, period=1.0)  # 5 req/sec
        async with limiter:
            await do_request()
    """

    def __init__(self, max_calls: int, period: float):
        self._max_calls = max_calls
        self._period = period
        self._semaphore = asyncio.Semaphore(max_calls)
        self._call_times: list[float] = []
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self._semaphore.acquire()
        async with self._lock:
            now = time.monotonic()
            # Remove expired timestamps
            self._call_times = [t for t in self._call_times if now - t < self._period]

            if len(self._call_times) >= self._max_calls:
                sleep_for = self._period - (now - self._call_times[0])
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)

            self._call_times.append(time.monotonic())
        return self

    async def __aexit__(self, *exc):
        self._semaphore.release()


class RateLimiterGroup:
    """Named collection of rate limiters."""

    def __init__(self):
        self._limiters: dict[str, AsyncRateLimiter] = {}

    def get(self, name: str, max_calls: int, period: float) -> AsyncRateLimiter:
        if name not in self._limiters:
            self._limiters[name] = AsyncRateLimiter(max_calls, period)
        return self._limiters[name]


# Global rate limiter group
rate_limiters = RateLimiterGroup()


def extract_domain(url: str) -> str:
    """Normalize a URL to its root domain only."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        # Strip www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        # Remove port if present
        domain = domain.split(":")[0]
        return domain.lower()
    except Exception:
        return ""
