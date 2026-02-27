"""
Sniper Execution Engine

Consumes MatchEvent objects from the shared queue, resolves
the Polymarket token via fuzzy mapping, checks profitability,
enforces risk limits, and fires market-buy orders.

Includes:
  - asyncio.Lock on the trade path to prevent race conditions
  - Error handling on market_buy (no record if order fails)
  - Position monitor: official API resolution + price-based fallback
  - Optional state persistence after every trade / resolution
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from adapters.base import MatchEvent
from core.circuit_breaker import CircuitBreaker
from core.config import settings
from core.mapper import mapper
from core.polymarket import polymarket
from core.risk import PositionRecord, RiskManager
from utils.alerts import send_alert

if TYPE_CHECKING:
    from core.persistence import StateStore

log = logging.getLogger("polysniper.engine")

POSITION_MONITOR_INTERVAL: float = 30.0
RESOLUTION_WIN_THRESHOLD: float = 0.95
RESOLUTION_LOSS_THRESHOLD: float = 0.05


class SniperEngine:
    def __init__(
        self,
        queue: asyncio.Queue[MatchEvent],
        risk: RiskManager | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self._queue = queue
        self._risk = risk or RiskManager()
        self._cb = circuit_breaker
        self._trades: list[dict] = []
        self._trade_lock = asyncio.Lock()
        self._state_store = state_store

    async def run(self) -> None:
        log.info("Sniper engine started — waiting for match events...")
        await mapper.refresh()

        refresh_task = asyncio.create_task(self._periodic_refresh())
        monitor_task = asyncio.create_task(self._monitor_positions())

        try:
            while True:
                event = await self._queue.get()
                asyncio.create_task(self._handle_event(event))
        except asyncio.CancelledError:
            refresh_task.cancel()
            monitor_task.cancel()
            self._save_state()
            log.info("Sniper engine stopped.")

    async def _periodic_refresh(self) -> None:
        while True:
            await asyncio.sleep(mapper.REFRESH_INTERVAL)
            try:
                await mapper.refresh()
            except Exception as exc:
                log.error("Market refresh failed: %s", exc)

    async def _handle_event(self, event: MatchEvent) -> None:
        t0 = time.perf_counter()
        log.info(
            "SIGNAL  %s | %s | %s won vs %s",
            event.game, event.event, event.team_won, event.team_lost,
        )

        if self._cb and self._cb.is_halted:
            log.warning("BLOCKED by circuit breaker — skipping event")
            return

        if self._risk.halted:
            log.warning("BLOCKED by risk manager (%s) — skipping event", self._risk.halt_reason)
            return

        mapping = mapper.find_token(event.team_won, event.game)
        if not mapping:
            log.warning("No Polymarket market found for '%s' (%s)", event.team_won, event.game)
            return

        token_id = mapping.token_id_yes
        ask = await polymarket.best_ask(token_id)

        if ask is None:
            log.warning("Empty order book for token %s", token_id[:12])
            return

        if ask > settings.trading.max_buy_price:
            log.info(
                "SKIP  ask=$%.3f > max=$%.2f for '%s'",
                ask, settings.trading.max_buy_price, mapping.question,
            )
            return

        amount = settings.trading.order_size_usdc

        # Critical section: check + buy + record must be atomic to prevent
        # concurrent duplicate trades on the same match.
        async with self._trade_lock:
            decision = self._risk.pre_trade_check(
                token_id=token_id,
                game=event.game,
                team=event.team_won,
                match_id=event.match_id,
                amount_usdc=amount,
                ask_price=ask,
            )
            if not decision:
                log.warning("RISK VETO: %s", decision.reason)
                return

            try:
                result = await polymarket.market_buy(token_id, amount)
            except Exception as exc:
                log.error(
                    "ORDER FAILED for %s %s: %s", event.game, event.team_won, exc,
                )
                await send_alert(
                    f"Order Failed\n"
                    f"Game: {event.game}\n"
                    f"Team: {event.team_won}\n"
                    f"Token: {token_id[:16]}\n"
                    f"Error: {exc}"
                )
                return

            self._risk.record_trade(
                token_id=token_id,
                game=event.game,
                team=event.team_won,
                match_id=event.match_id,
                amount_usdc=amount,
                buy_price=ask,
                condition_id=mapping.condition_id,
            )

        self._save_state()

        latency_ms = (time.perf_counter() - t0) * 1000
        trade_info = {
            "game": event.game,
            "team": event.team_won,
            "market": mapping.question,
            "ask_price": ask,
            "amount": amount,
            "latency_ms": round(latency_ms, 1),
            "dry_run": settings.trading.dry_run,
            "result": result,
            "open_positions": self._risk.open_positions,
            "total_exposure": self._risk.total_exposure,
        }
        self._trades.append(trade_info)

        mode = "DRY RUN" if settings.trading.dry_run else "LIVE"
        msg = (
            f"[{mode}] Trade executed\n"
            f"Game: {event.game}\n"
            f"Winner: {event.team_won}\n"
            f"Market: {mapping.question}\n"
            f"Ask: ${ask:.3f}\n"
            f"Size: ${amount:.2f}\n"
            f"Latency: {latency_ms:.1f}ms\n"
            f"Positions: {self._risk.open_positions} | "
            f"Exposure: ${self._risk.total_exposure:.2f}"
        )
        log.info(msg)
        await send_alert(f"Sniper Trade Executed\n{msg}")

    # ------------------------------------------------------------------
    # Position Monitor — tracks resolution and records realized PnL
    # ------------------------------------------------------------------
    async def _monitor_positions(self) -> None:
        """
        Background loop that checks open positions for resolution.

        Two resolution methods (tried in order):
          1. Official API — polymarket.get_market_resolution(condition_id)
          2. Price heuristic — best_ask >= 0.95 (win) or <= 0.05 (loss)
        """
        while True:
            await asyncio.sleep(POSITION_MONITOR_INTERVAL)
            await self._check_position_resolutions()

    async def _check_position_resolutions(self) -> None:
        positions = list(self._risk._positions)
        if not positions:
            return

        changed = False

        for pos in positions:
            try:
                pnl: float | None = None
                source = ""

                # ── 1. Official API resolution ──
                if pos.condition_id:
                    outcome = await polymarket.get_market_resolution(pos.condition_id)
                    if outcome is not None:
                        resolution_price = 1.0 if outcome.lower() == "yes" else 0.0
                        pnl = self._risk.close_position_with_pnl(pos.token_id, resolution_price)
                        source = "API"

                # ── 2. Price-based checks (resolution + stop-loss) ──
                if pnl is None:
                    price = await polymarket.best_ask(pos.token_id)
                    if price is None:
                        continue

                    if price >= RESOLUTION_WIN_THRESHOLD:
                        pnl = self._risk.close_position_with_pnl(pos.token_id, 1.0)
                        source = "price"

                    elif price <= RESOLUTION_LOSS_THRESHOLD:
                        pnl = self._risk.close_position_with_pnl(pos.token_id, 0.0)
                        source = "price"

                    elif self._should_stop_loss(pos, price):
                        pnl = await self._execute_stop_loss(pos, price)
                        source = "stop-loss"

                # ── 3. Alert ──
                if pnl is not None:
                    changed = True
                    await self._alert_position_closed(pos, pnl, source)

            except Exception as exc:
                log.debug("Position monitor error for %s: %s", pos.token_id[:12], exc)

        if changed:
            self._save_state()

    def _should_stop_loss(self, pos: PositionRecord, current_price: float) -> bool:
        sl = self._risk._cfg.stop_loss_pct
        if sl <= 0:
            return False
        trigger_price = pos.buy_price * (1.0 - sl)
        return current_price < trigger_price

    async def _execute_stop_loss(self, pos: PositionRecord, exit_price: float) -> float | None:
        """Sell the position at market and record the loss."""
        shares = pos.amount_usdc / pos.buy_price
        try:
            await polymarket.market_sell(pos.token_id, shares)
        except Exception as exc:
            log.error("STOP-LOSS SELL FAILED: %s %s: %s", pos.game, pos.team, exc)
            await send_alert(
                f"Stop-Loss Sell Failed\n"
                f"{pos.game} {pos.team}\n"
                f"Error: {exc}"
            )
            return None
        return self._risk.close_position_with_pnl(pos.token_id, exit_price, apply_fees=False)

    async def _alert_position_closed(
        self, pos: PositionRecord, pnl: float, source: str,
    ) -> None:
        if source == "stop-loss":
            log.warning(
                "STOP-LOSS  %s %s | PnL=$%.2f",
                pos.game, pos.team, pnl,
            )
            await send_alert(
                f"STOP-LOSS Triggered\n"
                f"{pos.game} {pos.team}\n"
                f"Bought@${pos.buy_price:.3f}\n"
                f"PnL: ${pnl:+.2f}"
            )
        else:
            won = pnl >= 0
            tag = "WIN" if won else "LOSS"
            if won:
                log.info("RESOLVED %s  %s %s | PnL=$%.2f (%s)", tag, pos.game, pos.team, pnl, source)
            else:
                log.warning("RESOLVED %s  %s %s | PnL=$%.2f (%s)", tag, pos.game, pos.team, pnl, source)
            await send_alert(
                f"Position Resolved {tag}\n"
                f"{pos.game} {pos.team}\n"
                f"Bought@${pos.buy_price:.3f} → ${'1.00' if won else '0.00'}\n"
                f"PnL: ${pnl:+.2f}\n"
                f"Source: {source}"
            )

    def _save_state(self) -> None:
        """Persist current risk state to disk (no-op if no store configured)."""
        if self._state_store:
            try:
                self._state_store.save(self._risk)
            except Exception as exc:
                log.error("Failed to save state: %s", exc)
