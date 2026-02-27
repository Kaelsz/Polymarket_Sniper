"""
Async Token Bucket Rate Limiter

Limits the rate of API calls to prevent bans. Uses the token bucket
algorithm: tokens refill at a steady rate, each call consumes one token.
If the bucket is empty, the caller waits until a token is available.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger("polysniper.rate_limiter")


class RateLimiter:
    """
    Async token bucket rate limiter.

    Args:
        rate: Maximum calls per second (e.g. 5.0 = 5 calls/sec)
        burst: Maximum burst size (bucket capacity)
    """

    def __init__(self, rate: float = 5.0, burst: int = 10) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._total_calls = 0
        self._total_waits = 0
        self._total_wait_time = 0.0

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def burst(self) -> int:
        return self._burst

    @property
    def stats(self) -> dict:
        return {
            "total_calls": self._total_calls,
            "total_waits": self._total_waits,
            "total_wait_time_s": round(self._total_wait_time, 3),
            "avg_wait_ms": round(self._total_wait_time / self._total_waits * 1000, 1) if self._total_waits else 0.0,
        }

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        async with self._lock:
            self._refill()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                self._total_calls += 1
                return

            deficit = 1.0 - self._tokens
            wait_time = deficit / self._rate

        log.debug("RATE LIMIT  waiting %.3fs for token", wait_time)
        self._total_waits += 1
        self._total_wait_time += wait_time
        await asyncio.sleep(wait_time)

        async with self._lock:
            self._refill()
            self._tokens = max(0.0, self._tokens - 1.0)
            self._total_calls += 1

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(
            float(self._burst),
            self._tokens + elapsed * self._rate,
        )
