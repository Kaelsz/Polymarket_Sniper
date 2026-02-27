from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.circuit_breaker import CircuitBreaker


@dataclass(frozen=True, slots=True)
class MatchEvent:
    """Standardised event emitted by every adapter."""

    game: str
    team_won: str
    event: str  # e.g. "Match Ended", "Map Won", "Series Won"
    match_id: str = ""
    team_lost: str = ""
    score: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class BaseAdapter(ABC):
    """
    Async base class for all esport live-data adapters.

    Subclasses must implement ``_connect`` and ``_listen``.
    The adapter pushes ``MatchEvent`` objects into an asyncio.Queue
    that the core engine consumes.
    """

    GAME: str = "UNKNOWN"
    RECONNECT_DELAY: float = 5.0
    MAX_RECONNECT_DELAY: float = 120.0

    def __init__(
        self,
        queue: asyncio.Queue[MatchEvent],
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._queue = queue
        self._cb = circuit_breaker
        self.log = logging.getLogger(f"polysniper.adapter.{self.GAME.lower()}")
        self._running = False

    async def emit(self, event: MatchEvent) -> None:
        self.log.info("EVENT  %s | %s beat %s", event.game, event.team_won, event.team_lost)
        await self._queue.put(event)
        if self._cb:
            self._cb.record_success(self.GAME)

    @abstractmethod
    async def _connect(self) -> None:
        """Establish connection to the live-data source."""

    @abstractmethod
    async def _listen(self) -> None:
        """
        Main loop that processes incoming data and calls ``self.emit()``.
        Must raise on disconnection so the reconnect logic kicks in.
        """

    async def run(self) -> None:
        """Run the adapter with automatic exponential-backoff reconnection."""
        self._running = True
        delay = self.RECONNECT_DELAY
        while self._running:
            try:
                self.log.info("Connecting to %s feed...", self.GAME)
                await self._connect()
                delay = self.RECONNECT_DELAY
                self.log.info("%s feed connected.", self.GAME)
                if self._cb:
                    self._cb.record_reconnect(self.GAME)
                await self._listen()
            except asyncio.CancelledError:
                self.log.info("%s adapter cancelled.", self.GAME)
                break
            except Exception as exc:
                self.log.error(
                    "%s adapter error: %s — reconnecting in %.1fs",
                    self.GAME,
                    exc,
                    delay,
                )
                if self._cb:
                    self._cb.record_failure(self.GAME, str(exc))
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.MAX_RECONNECT_DELAY)
        self._running = False

    def stop(self) -> None:
        self._running = False
