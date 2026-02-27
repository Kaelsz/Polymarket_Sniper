"""
PolySniper v1.0 — Entry Point

Launches 3 esport adapters (LoL, Valorant, Dota2) concurrently + the sniper
execution engine, with integrated risk management, circuit breaker, and state
persistence.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from adapters.base import MatchEvent
from adapters.dota2_adapter import Dota2Adapter
from adapters.lol_adapter import LoLAdapter
from adapters.valorant_adapter import ValorantAdapter
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from core.config import ConfigError, settings, validate_config
from core.dashboard import start_dashboard
from core.engine import SniperEngine
from core.persistence import StateStore
from core.polymarket import polymarket
from core.rate_limiter import RateLimiter
from core.risk import RiskConfig, RiskManager
from core.sizing import OrderSizer, SizingConfig
from utils.alerts import alert_crash, send_alert

LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))

_LOG_FMT = "%(asctime)s.%(msecs)03d | %(name)-30s | %(levelname)-5s | %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT)

    console = logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
    )
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "polysniper.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


_setup_logging()
log = logging.getLogger("polysniper")


def _build_risk_manager() -> RiskManager:
    cfg = settings.trading
    return RiskManager(RiskConfig(
        max_open_positions=cfg.max_open_positions,
        max_positions_per_game=cfg.max_positions_per_game,
        max_session_loss_usdc=cfg.max_session_loss_usdc,
        max_total_exposure_usdc=cfg.max_total_exposure_usdc,
        match_cooldown_seconds=cfg.match_cooldown_seconds,
        fee_rate=cfg.fee_rate,
        stop_loss_pct=cfg.stop_loss_pct,
    ))


def _build_circuit_breaker(risk: RiskManager) -> CircuitBreaker:
    cfg = settings.trading

    async def _on_halt() -> None:
        risk.halt("Circuit breaker triggered — too many adapter failures")
        await send_alert("CIRCUIT BREAKER OPEN — Trading halted")

    async def _on_resume() -> None:
        risk.resume()
        await send_alert("CIRCUIT BREAKER CLOSED — Trading resumed")

    return CircuitBreaker(
        config=CircuitBreakerConfig(
            failure_threshold=cfg.cb_failure_threshold,
            min_healthy_adapters=cfg.cb_min_healthy_adapters,
            stale_data_timeout=cfg.cb_stale_data_timeout,
        ),
        on_halt=_on_halt,
        on_resume=_on_resume,
    )


async def main() -> None:
    config_errors = validate_config(settings)
    if config_errors:
        for err in config_errors:
            log.error("CONFIG  %s", err)
        raise ConfigError(
            f"{len(config_errors)} configuration error(s) — fix .env and restart"
        )

    cfg = settings.trading
    log.info("=" * 60)
    log.info("  PolySniper v1.0 — Esport Latency Arbitrage Bot")
    log.info("  Dry run: %s", cfg.dry_run)
    log.info("  Max buy price: $%.2f", cfg.max_buy_price)
    log.info("  Order sizing: %s (base=$%.2f, range=$%.2f–$%.2f)",
             cfg.sizing_mode, cfg.order_size_usdc, cfg.min_order_usdc, cfg.max_order_usdc)
    log.info("  Max positions: %d (per game: %d)", cfg.max_open_positions, cfg.max_positions_per_game)
    log.info("  Max exposure: $%.2f | Max session loss: $%.2f", cfg.max_total_exposure_usdc, cfg.max_session_loss_usdc)
    log.info("  Circuit breaker: fail_threshold=%d, min_healthy=%d", cfg.cb_failure_threshold, cfg.cb_min_healthy_adapters)
    log.info("  Fee rate: %.1f%% | Stop-loss: %s", cfg.fee_rate * 100, f"{cfg.stop_loss_pct:.0%}" if cfg.stop_loss_pct > 0 else "disabled")
    log.info("  Rate limit: %.1f req/s (burst=%d)", cfg.api_rate_limit, cfg.api_rate_burst)
    log.info("  Dashboard: http://0.0.0.0:%d", cfg.dashboard_port)
    log.info("=" * 60)

    limiter = RateLimiter(rate=cfg.api_rate_limit, burst=cfg.api_rate_burst)
    polymarket.set_rate_limiter(limiter)
    await polymarket.init()

    risk = _build_risk_manager()
    cb = _build_circuit_breaker(risk)
    state_store = StateStore()

    if state_store.load(risk):
        log.info(
            "Restored state: %d positions, PnL=$%.2f",
            risk.open_positions, risk.session_pnl,
        )

    event_queue: asyncio.Queue[MatchEvent] = asyncio.Queue()

    adapters = [
        LoLAdapter(event_queue, circuit_breaker=cb),
        ValorantAdapter(event_queue, circuit_breaker=cb),
        Dota2Adapter(event_queue, circuit_breaker=cb),
    ]

    for a in adapters:
        cb.register(a.GAME)

    sizer = OrderSizer(SizingConfig(
        mode=cfg.sizing_mode,
        base_size=cfg.order_size_usdc,
        min_order=cfg.min_order_usdc,
        max_order=cfg.max_order_usdc,
        kelly_fraction=cfg.kelly_fraction,
        kelly_win_prob=cfg.kelly_win_prob,
    ))

    engine = SniperEngine(event_queue, risk=risk, circuit_breaker=cb, state_store=state_store, sizer=sizer)

    dashboard_runner = await start_dashboard(risk, cb, engine, port=cfg.dashboard_port, limiter=limiter)

    tasks = [asyncio.create_task(a.run(), name=a.GAME) for a in adapters]
    tasks.append(asyncio.create_task(engine.run(), name="Engine"))
    tasks.append(asyncio.create_task(cb.monitor_loop(), name="CircuitBreaker"))

    def _shutdown() -> None:
        log.info("Shutdown signal received — saving state and stopping...")
        state_store.save(risk)
        for a in adapters:
            a.stop()
        for t in tasks:
            t.cancel()

    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _shutdown)

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as exc:
        log.critical("Fatal error: %s", exc)
        await alert_crash(str(exc))
        raise
    finally:
        await dashboard_runner.cleanup()
        state_store.save(risk)
        log.info("Final state saved.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("PolySniper stopped by user.")
