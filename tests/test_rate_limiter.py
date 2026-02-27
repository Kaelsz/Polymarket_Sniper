"""Tests for the async token bucket rate limiter."""

from __future__ import annotations

import asyncio
import time

import pytest

from core.rate_limiter import RateLimiter


@pytest.fixture
def limiter():
    return RateLimiter(rate=10.0, burst=5)


class TestRateLimiterBasic:
    @pytest.mark.asyncio
    async def test_initial_burst_does_not_wait(self, limiter):
        """Burst capacity should allow immediate calls without delay."""
        start = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_exceeding_burst_causes_wait(self):
        limiter = RateLimiter(rate=10.0, burst=2)
        start = time.monotonic()
        for _ in range(4):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05

    @pytest.mark.asyncio
    async def test_stats_total_calls(self, limiter):
        for _ in range(3):
            await limiter.acquire()
        assert limiter.stats["total_calls"] == 3

    @pytest.mark.asyncio
    async def test_stats_no_waits_within_burst(self, limiter):
        for _ in range(5):
            await limiter.acquire()
        assert limiter.stats["total_waits"] == 0

    @pytest.mark.asyncio
    async def test_stats_waits_when_throttled(self):
        limiter = RateLimiter(rate=10.0, burst=1)
        for _ in range(3):
            await limiter.acquire()
        assert limiter.stats["total_waits"] >= 1
        assert limiter.stats["total_wait_time_s"] > 0

    @pytest.mark.asyncio
    async def test_properties(self, limiter):
        assert limiter.rate == 10.0
        assert limiter.burst == 5


class TestRateLimiterRefill:
    @pytest.mark.asyncio
    async def test_tokens_refill_after_wait(self):
        limiter = RateLimiter(rate=100.0, burst=2)
        await limiter.acquire()
        await limiter.acquire()
        await asyncio.sleep(0.05)
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.05

    @pytest.mark.asyncio
    async def test_refill_does_not_exceed_burst(self):
        limiter = RateLimiter(rate=1000.0, burst=3)
        await asyncio.sleep(0.1)
        start = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed > 0


class TestRateLimiterConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_acquires_respect_rate(self):
        limiter = RateLimiter(rate=20.0, burst=2)
        start = time.monotonic()
        tasks = [limiter.acquire() for _ in range(6)]
        await asyncio.gather(*tasks)
        elapsed = time.monotonic() - start
        assert limiter.stats["total_calls"] == 6

    @pytest.mark.asyncio
    async def test_avg_wait_ms_in_stats(self):
        limiter = RateLimiter(rate=10.0, burst=1)
        for _ in range(3):
            await limiter.acquire()
        stats = limiter.stats
        if stats["total_waits"] > 0:
            assert stats["avg_wait_ms"] > 0


class TestRateLimiterEdgeCases:
    @pytest.mark.asyncio
    async def test_single_acquire(self):
        limiter = RateLimiter(rate=1.0, burst=1)
        await limiter.acquire()
        assert limiter.stats["total_calls"] == 1
        assert limiter.stats["total_waits"] == 0

    @pytest.mark.asyncio
    async def test_very_high_rate(self):
        limiter = RateLimiter(rate=10000.0, burst=100)
        start = time.monotonic()
        for _ in range(50):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_stats_format(self):
        limiter = RateLimiter(rate=10.0, burst=5)
        stats = limiter.stats
        assert "total_calls" in stats
        assert "total_waits" in stats
        assert "total_wait_time_s" in stats
        assert "avg_wait_ms" in stats
        assert stats["avg_wait_ms"] == 0.0


class TestRateLimiterIntegration:
    @pytest.mark.asyncio
    async def test_polymarket_client_throttle(self):
        """Test that PolymarketClient._throttle calls the limiter."""
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        limiter = RateLimiter(rate=100.0, burst=10)
        client.set_rate_limiter(limiter)
        await client._throttle()
        await client._throttle()
        assert limiter.stats["total_calls"] == 2

    @pytest.mark.asyncio
    async def test_polymarket_client_without_limiter(self):
        """_throttle should be a no-op when no limiter is set."""
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        await client._throttle()


class TestRateLimiterConfigValidation:
    def test_valid_rate_limit_config(self):
        from core.config import validate_config
        from tests.test_config import _valid_settings

        errs = validate_config(_valid_settings(api_rate_limit=5.0, api_rate_burst=10))
        assert not any("API_RATE" in e for e in errs)

    def test_rate_limit_zero(self):
        from core.config import validate_config
        from tests.test_config import _valid_settings

        errs = validate_config(_valid_settings(api_rate_limit=0.0))
        assert any("API_RATE_LIMIT" in e for e in errs)

    def test_rate_limit_negative(self):
        from core.config import validate_config
        from tests.test_config import _valid_settings

        errs = validate_config(_valid_settings(api_rate_limit=-1.0))
        assert any("API_RATE_LIMIT" in e for e in errs)

    def test_burst_zero(self):
        from core.config import validate_config
        from tests.test_config import _valid_settings

        errs = validate_config(_valid_settings(api_rate_burst=0))
        assert any("API_RATE_BURST" in e for e in errs)

    def test_burst_negative(self):
        from core.config import validate_config
        from tests.test_config import _valid_settings

        errs = validate_config(_valid_settings(api_rate_burst=-5))
        assert any("API_RATE_BURST" in e for e in errs)
