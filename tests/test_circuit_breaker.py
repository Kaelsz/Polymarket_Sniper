from __future__ import annotations

import asyncio
import time

import pytest

from core.circuit_breaker import (
    AdapterHealth,
    AdapterState,
    CircuitBreaker,
    CircuitBreakerConfig,
)


def _cfg(**overrides) -> CircuitBreakerConfig:
    defaults = dict(
        failure_threshold=3,
        recovery_timeout_seconds=10.0,
        min_healthy_adapters=2,
        health_check_interval=1.0,
        stale_data_timeout=5.0,
    )
    defaults.update(overrides)
    return CircuitBreakerConfig(**defaults)


class TestAdapterRegistration:
    def test_register_adapter(self):
        cb = CircuitBreaker(_cfg())
        cb.register("CS2")
        assert "CS2" in cb.adapter_states
        assert cb.adapter_states["CS2"] == "CLOSED"

    def test_register_multiple(self):
        cb = CircuitBreaker(_cfg())
        for name in ("CS2", "LoL", "Valorant", "Dota2"):
            cb.register(name)
        assert cb.healthy_count == 4

    def test_initial_state(self):
        cb = CircuitBreaker(_cfg())
        assert not cb.is_halted
        assert cb.healthy_count == 0


class TestRecordSuccess:
    def test_success_keeps_closed(self):
        cb = CircuitBreaker(_cfg())
        cb.register("CS2")
        cb.record_success("CS2")
        assert cb.adapter_states["CS2"] == "CLOSED"
        health = cb.get_health("CS2")
        assert health.total_events == 1

    def test_success_recovers_from_open(self):
        cb = CircuitBreaker(_cfg())
        cb.register("CS2")
        health = cb.get_health("CS2")
        health.state = AdapterState.OPEN
        cb.record_success("CS2")
        assert cb.adapter_states["CS2"] == "CLOSED"

    def test_success_on_unknown_adapter(self):
        cb = CircuitBreaker(_cfg())
        cb.record_success("Unknown")  # should not raise


class TestRecordFailure:
    def test_single_failure_stays_closed(self):
        cb = CircuitBreaker(_cfg(failure_threshold=3))
        cb.register("CS2")
        cb.record_failure("CS2", "timeout")
        assert cb.adapter_states["CS2"] == "CLOSED"
        assert cb.get_health("CS2").consecutive_failures == 1

    def test_threshold_triggers_open(self):
        cb = CircuitBreaker(_cfg(failure_threshold=3))
        cb.register("CS2")
        cb.record_failure("CS2", "err1")
        cb.record_failure("CS2", "err2")
        cb.record_failure("CS2", "err3")
        assert cb.adapter_states["CS2"] == "OPEN"

    def test_failure_increments_total(self):
        cb = CircuitBreaker(_cfg(failure_threshold=10))
        cb.register("CS2")
        for i in range(5):
            cb.record_failure("CS2", f"err{i}")
        assert cb.get_health("CS2").total_failures == 5

    def test_failure_on_unknown_adapter(self):
        cb = CircuitBreaker(_cfg())
        cb.record_failure("Unknown")  # should not raise


class TestRecordReconnect:
    def test_reconnect_from_open(self):
        cb = CircuitBreaker(_cfg())
        cb.register("CS2")
        health = cb.get_health("CS2")
        health.state = AdapterState.OPEN
        cb.record_reconnect("CS2")
        assert cb.adapter_states["CS2"] == "HALF_OPEN"

    def test_reconnect_from_closed_stays_closed(self):
        cb = CircuitBreaker(_cfg())
        cb.register("CS2")
        cb.record_reconnect("CS2")
        assert cb.adapter_states["CS2"] == "CLOSED"


class TestGlobalHalt:
    def test_halt_when_too_few_healthy(self):
        cb = CircuitBreaker(_cfg(failure_threshold=1, min_healthy_adapters=2))
        cb.register("CS2")
        cb.register("LoL")
        cb.register("Valorant")
        cb.record_failure("CS2", "err")
        cb.record_failure("LoL", "err")
        cb.record_failure("Valorant", "err")
        assert cb.is_halted

    def test_no_halt_with_enough_healthy(self):
        cb = CircuitBreaker(_cfg(failure_threshold=1, min_healthy_adapters=2))
        cb.register("CS2")
        cb.register("LoL")
        cb.register("Valorant")
        cb.record_failure("CS2", "err")
        assert not cb.is_halted  # still 2 healthy

    def test_resume_when_adapters_recover(self):
        cb = CircuitBreaker(_cfg(failure_threshold=1, min_healthy_adapters=2))
        cb.register("CS2")
        cb.register("LoL")
        cb.register("Valorant")
        cb.record_failure("CS2", "err")
        cb.record_failure("LoL", "err")
        cb.record_failure("Valorant", "err")
        assert cb.is_halted
        cb.record_success("CS2")
        cb.record_success("LoL")
        assert not cb.is_halted

    def test_halt_with_single_adapter_requirement(self):
        cb = CircuitBreaker(_cfg(failure_threshold=1, min_healthy_adapters=1))
        cb.register("CS2")
        cb.record_failure("CS2", "err")
        assert cb.is_halted

    def test_no_halt_all_healthy(self):
        cb = CircuitBreaker(_cfg(min_healthy_adapters=1))
        cb.register("CS2")
        cb.register("LoL")
        cb.record_success("CS2")
        cb.record_success("LoL")
        assert not cb.is_halted


class TestStaleDetection:
    def test_stale_adapter_opens(self):
        cb = CircuitBreaker(_cfg(stale_data_timeout=0.0))
        cb.register("CS2")
        health = cb.get_health("CS2")
        health.last_event_time = time.time() - 1.0
        stale = cb.check_stale()
        assert "CS2" in stale
        assert cb.adapter_states["CS2"] == "OPEN"

    def test_fresh_adapter_stays_closed(self):
        cb = CircuitBreaker(_cfg(stale_data_timeout=999.0))
        cb.register("CS2")
        stale = cb.check_stale()
        assert stale == []
        assert cb.adapter_states["CS2"] == "CLOSED"

    def test_stale_only_affects_closed(self):
        cb = CircuitBreaker(_cfg(stale_data_timeout=0.0))
        cb.register("CS2")
        health = cb.get_health("CS2")
        health.state = AdapterState.OPEN
        health.last_event_time = time.time() - 999.0
        stale = cb.check_stale()
        assert stale == []

    def test_stale_triggers_halt(self):
        cb = CircuitBreaker(_cfg(stale_data_timeout=0.0, min_healthy_adapters=1))
        cb.register("CS2")
        health = cb.get_health("CS2")
        health.last_event_time = time.time() - 1.0
        cb.check_stale()
        assert cb.is_halted


class TestCallbacks:
    @pytest.mark.asyncio
    async def test_on_halt_called(self):
        halt_called = []

        async def on_halt():
            halt_called.append(True)

        cb = CircuitBreaker(
            _cfg(failure_threshold=1, min_healthy_adapters=1),
            on_halt=on_halt,
        )
        cb.register("CS2")
        cb.record_failure("CS2", "err")
        await asyncio.sleep(0.05)
        assert halt_called

    @pytest.mark.asyncio
    async def test_on_resume_called(self):
        resume_called = []

        async def on_resume():
            resume_called.append(True)

        cb = CircuitBreaker(
            _cfg(failure_threshold=1, min_healthy_adapters=1),
            on_resume=on_resume,
        )
        cb.register("CS2")
        cb.record_failure("CS2", "err")
        await asyncio.sleep(0.05)
        cb.record_success("CS2")
        await asyncio.sleep(0.05)
        assert resume_called


class TestSummary:
    def test_summary_format(self):
        cb = CircuitBreaker(_cfg())
        cb.register("CS2")
        cb.register("LoL")
        s = cb.summary()
        assert "CS2" in s
        assert "LoL" in s
        assert "CLOSED" in s


class TestHealthyCount:
    def test_counts_only_closed(self):
        cb = CircuitBreaker(_cfg(failure_threshold=1))
        cb.register("CS2")
        cb.register("LoL")
        cb.register("Valorant")
        cb.record_failure("CS2", "err")
        assert cb.healthy_count == 2

    def test_half_open_not_counted(self):
        cb = CircuitBreaker(_cfg())
        cb.register("CS2")
        health = cb.get_health("CS2")
        health.state = AdapterState.HALF_OPEN
        assert cb.healthy_count == 0
