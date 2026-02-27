"""
Circuit Breaker — Adapter Health Monitor

Tracks adapter health and halts trading when too many feeds fail.

States per adapter:
  CLOSED   — healthy, data flowing
  OPEN     — failed, not receiving data
  HALF_OPEN — recovering, waiting for confirmation

Global policy:
  If >= ``min_healthy_adapters`` are OPEN simultaneously, halt trading.
  Auto-resume when enough adapters recover.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("polysniper.circuit_breaker")


class AdapterState(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(slots=True)
class CircuitBreakerConfig:
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 60.0
    min_healthy_adapters: int = 1
    health_check_interval: float = 10.0
    stale_data_timeout: float = 120.0


@dataclass
class AdapterHealth:
    name: str
    state: AdapterState = AdapterState.CLOSED
    consecutive_failures: int = 0
    last_success: float = field(default_factory=time.time)
    last_failure: float = 0.0
    last_event_time: float = field(default_factory=time.time)
    total_events: int = 0
    total_failures: int = 0


class CircuitBreaker:
    """
    Monitors adapter health and triggers a global trading halt
    when too many data feeds are down.
    """

    def __init__(
        self,
        config: CircuitBreakerConfig | None = None,
        on_halt: asyncio.coroutines | None = None,
        on_resume: asyncio.coroutines | None = None,
    ) -> None:
        self._cfg = config or CircuitBreakerConfig()
        self._adapters: dict[str, AdapterHealth] = {}
        self._global_halt = False
        self._on_halt = on_halt
        self._on_resume = on_resume

    @property
    def is_halted(self) -> bool:
        return self._global_halt

    @property
    def healthy_count(self) -> int:
        return sum(
            1 for a in self._adapters.values()
            if a.state == AdapterState.CLOSED
        )

    @property
    def adapter_states(self) -> dict[str, str]:
        return {name: a.state.value for name, a in self._adapters.items()}

    def register(self, adapter_name: str) -> None:
        """Register an adapter for health tracking."""
        self._adapters[adapter_name] = AdapterHealth(name=adapter_name)
        log.info("CB  Registered adapter: %s", adapter_name)

    def record_success(self, adapter_name: str) -> None:
        """Record a successful event from an adapter."""
        health = self._adapters.get(adapter_name)
        if not health:
            return

        now = time.time()
        health.last_success = now
        health.last_event_time = now
        health.total_events += 1

        if health.state == AdapterState.OPEN:
            health.state = AdapterState.HALF_OPEN
            log.info("CB  %s: OPEN -> HALF_OPEN (received data)", adapter_name)

        if health.state == AdapterState.HALF_OPEN:
            health.consecutive_failures = 0
            health.state = AdapterState.CLOSED
            log.info("CB  %s: HALF_OPEN -> CLOSED (recovered)", adapter_name)
            self._evaluate_global_state()

    def record_failure(self, adapter_name: str, error: str = "") -> None:
        """Record a failure (disconnect, exception) from an adapter."""
        health = self._adapters.get(adapter_name)
        if not health:
            return

        now = time.time()
        health.consecutive_failures += 1
        health.total_failures += 1
        health.last_failure = now

        log.warning(
            "CB  %s: failure #%d — %s",
            adapter_name, health.consecutive_failures, error or "unknown",
        )

        if (
            health.state == AdapterState.CLOSED
            and health.consecutive_failures >= self._cfg.failure_threshold
        ):
            health.state = AdapterState.OPEN
            log.error(
                "CB  %s: CLOSED -> OPEN (threshold %d reached)",
                adapter_name, self._cfg.failure_threshold,
            )
            self._evaluate_global_state()

    def record_heartbeat(self, adapter_name: str) -> None:
        """Update last-seen timestamp without counting as a match event.

        Adapters should call this on each successful poll cycle so the
        stale-data detector knows the feed is alive even when no matches
        are finishing.
        """
        health = self._adapters.get(adapter_name)
        if not health:
            return
        health.last_event_time = time.time()

    def record_reconnect(self, adapter_name: str) -> None:
        """Called when an adapter successfully reconnects."""
        health = self._adapters.get(adapter_name)
        if not health:
            return

        if health.state == AdapterState.OPEN:
            health.state = AdapterState.HALF_OPEN
            health.last_success = time.time()
            log.info("CB  %s: OPEN -> HALF_OPEN (reconnected)", adapter_name)

    def check_stale(self) -> list[str]:
        """Check for adapters that haven't sent data recently."""
        now = time.time()
        stale: list[str] = []
        for name, health in self._adapters.items():
            if health.state == AdapterState.CLOSED:
                elapsed = now - health.last_event_time
                if elapsed > self._cfg.stale_data_timeout:
                    stale.append(name)
                    health.state = AdapterState.OPEN
                    log.warning(
                        "CB  %s: CLOSED -> OPEN (stale: no data for %.0fs)",
                        name, elapsed,
                    )
        if stale:
            self._evaluate_global_state()
        return stale

    def get_health(self, adapter_name: str) -> AdapterHealth | None:
        return self._adapters.get(adapter_name)

    def _evaluate_global_state(self) -> None:
        """Decide whether to halt or resume trading globally."""
        healthy = self.healthy_count
        total = len(self._adapters)
        open_count = sum(
            1 for a in self._adapters.values()
            if a.state == AdapterState.OPEN
        )

        if not self._global_halt and healthy < self._cfg.min_healthy_adapters:
            self._global_halt = True
            log.critical(
                "CB  GLOBAL HALT — only %d/%d adapters healthy (need %d)",
                healthy, total, self._cfg.min_healthy_adapters,
            )
            if self._on_halt:
                asyncio.ensure_future(self._on_halt())

        elif self._global_halt and healthy >= self._cfg.min_healthy_adapters:
            self._global_halt = False
            log.warning(
                "CB  GLOBAL RESUME — %d/%d adapters healthy",
                healthy, total,
            )
            if self._on_resume:
                asyncio.ensure_future(self._on_resume())

    async def monitor_loop(self) -> None:
        """Background loop that periodically checks for stale adapters."""
        while True:
            await asyncio.sleep(self._cfg.health_check_interval)
            self.check_stale()

    def summary(self) -> str:
        lines = [f"Circuit Breaker — halted={self._global_halt}"]
        for name, h in self._adapters.items():
            lines.append(
                f"  {name:12s}  state={h.state.value:10s}  "
                f"failures={h.consecutive_failures}  events={h.total_events}"
            )
        return "\n".join(lines)
