from __future__ import annotations

import asyncio

import pytest

from adapters.base import BaseAdapter, MatchEvent


class DummyAdapter(BaseAdapter):
    """Concrete adapter for testing the abstract base."""

    GAME = "TestGame"
    RECONNECT_DELAY = 0.01
    MAX_RECONNECT_DELAY = 0.05

    def __init__(self, queue: asyncio.Queue, fail_count: int = 0) -> None:
        super().__init__(queue)
        self._fail_count = fail_count
        self._connect_calls = 0
        self._listen_calls = 0

    async def _connect(self) -> None:
        self._connect_calls += 1
        if self._connect_calls <= self._fail_count:
            raise ConnectionError(f"Simulated failure #{self._connect_calls}")

    async def _listen(self) -> None:
        self._listen_calls += 1
        self.stop()


class TestBaseAdapterEmit:
    @pytest.mark.asyncio
    async def test_emit_puts_event_on_queue(self, event_queue):
        adapter = DummyAdapter(event_queue)
        ev = MatchEvent(game="TestGame", team_won="Alpha", event="Test Win")
        await adapter.emit(ev)
        assert not event_queue.empty()
        result = await event_queue.get()
        assert result.team_won == "Alpha"

    @pytest.mark.asyncio
    async def test_emit_multiple_events(self, event_queue):
        adapter = DummyAdapter(event_queue)
        for i in range(5):
            await adapter.emit(MatchEvent(game="Test", team_won=f"T{i}", event="Win"))
        assert event_queue.qsize() == 5


class TestBaseAdapterRun:
    @pytest.mark.asyncio
    async def test_run_connects_and_listens(self, event_queue):
        adapter = DummyAdapter(event_queue)
        await adapter.run()
        assert adapter._connect_calls == 1
        assert adapter._listen_calls == 1

    @pytest.mark.asyncio
    async def test_run_reconnects_on_failure(self, event_queue):
        adapter = DummyAdapter(event_queue, fail_count=2)
        await adapter.run()
        assert adapter._connect_calls == 3
        assert adapter._listen_calls == 1

    @pytest.mark.asyncio
    async def test_run_cancellation(self, event_queue):
        class HangingAdapter(BaseAdapter):
            GAME = "Hang"

            async def _connect(self) -> None:
                pass

            async def _listen(self) -> None:
                await asyncio.sleep(999)

        adapter = HangingAdapter(event_queue)
        task = asyncio.create_task(adapter.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not adapter._running


class TestBaseAdapterStop:
    @pytest.mark.asyncio
    async def test_stop_flag(self, event_queue):
        adapter = DummyAdapter(event_queue)
        adapter._running = True
        adapter.stop()
        assert not adapter._running


class TestBaseAdapterCircuitBreaker:
    @pytest.mark.asyncio
    async def test_emit_reports_success_to_cb(self, event_queue):
        from unittest.mock import MagicMock

        cb = MagicMock()
        adapter = DummyAdapter(event_queue)
        adapter._cb = cb
        ev = MatchEvent(game="Test", team_won="A", event="Win")
        await adapter.emit(ev)
        cb.record_success.assert_called_once_with("TestGame")

    @pytest.mark.asyncio
    async def test_failure_reports_to_cb(self, event_queue):
        from unittest.mock import MagicMock

        cb = MagicMock()

        class FailAdapter(BaseAdapter):
            GAME = "Fail"
            RECONNECT_DELAY = 0.01
            MAX_RECONNECT_DELAY = 0.01

            def __init__(self, q, circuit_breaker):
                super().__init__(q, circuit_breaker=circuit_breaker)
                self._attempts = 0

            async def _connect(self):
                self._attempts += 1
                if self._attempts <= 1:
                    raise ConnectionError("boom")

            async def _listen(self):
                self.stop()

        adapter = FailAdapter(event_queue, circuit_breaker=cb)
        await adapter.run()
        cb.record_failure.assert_called_once()
        cb.record_reconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_cb_no_crash(self, event_queue):
        adapter = DummyAdapter(event_queue)
        ev = MatchEvent(game="Test", team_won="A", event="Win")
        await adapter.emit(ev)
        await adapter.run()
