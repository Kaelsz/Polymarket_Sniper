"""
Sniper Execution Engine

Consumes Opportunity objects from the scanner queue, verifies
prices on the CLOB, enforces risk limits, and fires market-buy orders.

Includes:
  - asyncio.Lock on the trade path to prevent race conditions
  - Error handling on market_buy (no record if order fails)
  - Position monitor: official API resolution + price-based fallback
  - Optional state persistence after every trade / resolution
  - Scanner callback: clears _seen on rejection so tokens can be retried
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Callable

from core.circuit_breaker import CircuitBreaker
from core.claimer import PositionClaimer
from core.config import settings
from core.polymarket import polymarket
from core.risk import PositionRecord, RiskManager
from core.scanner import Opportunity
from core.sizing import OrderSizer
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
        queue: asyncio.Queue[Opportunity],
        risk: RiskManager | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        state_store: StateStore | None = None,
        sizer: OrderSizer | None = None,
        on_reject: Callable[[str], None] | None = None,
        claimer: PositionClaimer | None = None,
    ) -> None:
        self._queue = queue
        self._risk = risk or RiskManager()
        self._cb = circuit_breaker
        self._trades: list[dict] = []
        self._trade_lock = asyncio.Lock()
        self._state_store = state_store
        self._sizer = sizer or OrderSizer()
        self._on_reject = on_reject
        self._claimer = claimer

    async def run(self) -> None:
        log.info("Sniper engine started — waiting for opportunities...")

        monitor_task = asyncio.create_task(self._monitor_positions())

        try:
            while True:
                opp = await self._queue.get()
                asyncio.create_task(self._handle_opportunity(opp))
        except asyncio.CancelledError:
            monitor_task.cancel()
            self._save_state()
            log.info("Sniper engine stopped.")

    def _reject(self, token_id: str) -> None:
        """Notify the scanner that a token was rejected so it can be retried."""
        if self._on_reject:
            self._on_reject(token_id)

    async def _handle_opportunity(self, opp: Opportunity) -> None:
        t0 = time.perf_counter()
        log.info(
            "SIGNAL  %s @ $%.4f (gamma) | %s | vol=$%.0fK",
            opp.outcome, opp.ask_price, opp.question[:60], opp.volume / 1000,
        )

        if self._cb and self._cb.is_halted:
            log.warning("BLOCKED by circuit breaker — skipping")
            self._reject(opp.token_id)
            return

        if self._risk.halted:
            log.warning("BLOCKED by risk manager (%s) — skipping", self._risk.halt_reason)
            self._reject(opp.token_id)
            return

        try:
            ask = await polymarket.best_ask(opp.token_id)
        except Exception as exc:
            log.warning("CLOB error for token %s: %s", opp.token_id[:12], exc)
            self._reject(opp.token_id)
            return

        if ask is None:
            log.warning("Empty order book for token %s", opp.token_id[:12])
            self._reject(opp.token_id)
            return

        if ask < settings.trading.min_buy_price:
            log.info(
                "SKIP  CLOB ask=$%.4f < min=$%.2f for '%s'",
                ask, settings.trading.min_buy_price, opp.question[:60],
            )
            self._reject(opp.token_id)
            return

        if ask > settings.trading.max_buy_price:
            log.info(
                "SKIP  CLOB ask=$%.4f > max=$%.2f for '%s'",
                ask, settings.trading.max_buy_price, opp.question[:60],
            )
            self._reject(opp.token_id)
            return

        async with self._trade_lock:
            max_pos = self._risk._cfg.max_open_positions
            open_pos = self._risk.open_positions
            available_slots = max_pos - open_pos
            if available_slots <= 0:
                log.info("SKIP  No available slots (%d/%d)", open_pos, max_pos)
                self._reject(opp.token_id)
                return

            balance = await polymarket.get_balance_usdc()
            if balance < 5.0:
                log.warning("SKIP  Insufficient balance: $%.2f", balance)
                self._reject(opp.token_id)
                return

            amount = round(balance / available_slots, 2)
            amount = max(5.0, amount)
            log.info(
                "SIZING  balance=$%.2f, slots=%d/%d → bet=$%.2f",
                balance, open_pos, max_pos, amount,
            )

            decision = self._risk.pre_trade_check(
                token_id=opp.token_id,
                game="market",
                team=opp.outcome,
                match_id=opp.condition_id,
                amount_usdc=amount,
                ask_price=ask,
            )
            if not decision:
                log.warning("RISK VETO: %s", decision.reason)
                return

            try:
                result = await polymarket.market_buy(opp.token_id, amount)
            except Exception as exc:
                log.error(
                    "ORDER FAILED for %s: %s", opp.question[:60], exc,
                )
                await send_alert(
                    f"Order Failed\n"
                    f"Market: {opp.question}\n"
                    f"Token: {opp.token_id[:16]}\n"
                    f"Error: {exc}"
                )
                self._reject(opp.token_id)
                return

            self._risk.record_trade(
                token_id=opp.token_id,
                game="market",
                team=opp.outcome,
                match_id=opp.condition_id,
                amount_usdc=amount,
                buy_price=ask,
                condition_id=opp.condition_id,
            )

        self._save_state()

        latency_ms = (time.perf_counter() - t0) * 1000
        trade_info = {
            "game": "market",
            "team": opp.outcome,
            "market": opp.question,
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
            f"Market: {opp.question}\n"
            f"Outcome: {opp.outcome}\n"
            f"Ask: ${ask:.4f}\n"
            f"Size: ${amount:.2f}\n"
            f"Latency: {latency_ms:.1f}ms\n"
            f"Volume: ${opp.volume/1000:.0f}K\n"
            f"Positions: {self._risk.open_positions} | "
            f"Exposure: ${self._risk.total_exposure:.2f}"
        )
        log.info(msg)
        await send_alert(f"Sniper Trade Executed\n{msg}")

    # ------------------------------------------------------------------
    # Position Monitor — tracks resolution and records realized PnL
    # ------------------------------------------------------------------
    async def _monitor_positions(self) -> None:
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

                if pos.condition_id:
                    outcome = await polymarket.get_market_resolution(pos.condition_id)
                    if outcome is not None:
                        resolution_price = 1.0 if outcome.lower() == "yes" else 0.0
                        pnl = self._risk.close_position_with_pnl(pos.token_id, resolution_price)
                        source = "API"

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

                if pnl is not None:
                    changed = True
                    if pnl > 0 and pos.condition_id and self._claimer:
                        await self._auto_redeem(pos)
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
        shares = pos.amount_usdc / pos.buy_price
        try:
            await polymarket.market_sell(pos.token_id, shares)
        except Exception as exc:
            log.error("STOP-LOSS SELL FAILED: %s: %s", pos.team, exc)
            await send_alert(
                f"Stop-Loss Sell Failed\n"
                f"Market: {pos.team}\n"
                f"Error: {exc}"
            )
            return None
        return self._risk.close_position_with_pnl(pos.token_id, exit_price, apply_fees=False)

    async def _alert_position_closed(
        self, pos: PositionRecord, pnl: float, source: str,
    ) -> None:
        if source == "stop-loss":
            log.warning(
                "STOP-LOSS  %s | PnL=$%.2f", pos.team, pnl,
            )
            await send_alert(
                f"STOP-LOSS Triggered\n"
                f"Market: {pos.team}\n"
                f"Bought@${pos.buy_price:.4f}\n"
                f"PnL: ${pnl:+.2f}"
            )
        else:
            won = pnl >= 0
            tag = "WIN" if won else "LOSS"
            if won:
                log.info("RESOLVED %s  %s | PnL=$%.2f (%s)", tag, pos.team, pnl, source)
            else:
                log.warning("RESOLVED %s  %s | PnL=$%.2f (%s)", tag, pos.team, pnl, source)
            await send_alert(
                f"Position Resolved {tag}\n"
                f"Market: {pos.team}\n"
                f"Bought@${pos.buy_price:.4f} → ${'1.00' if won else '0.00'}\n"
                f"PnL: ${pnl:+.2f}\n"
                f"Source: {source}"
            )

    async def _auto_redeem(self, pos: PositionRecord) -> None:
        """Auto-claim USDC.e from a resolved winning position."""
        log.info("AUTO-REDEEM starting for %s (%s)", pos.team, pos.condition_id[:16])
        try:
            receipt = await self._claimer.redeem(pos.condition_id)
            if receipt:
                await send_alert(
                    f"Auto-Claim OK\n"
                    f"Market: {pos.team}\n"
                    f"Condition: {pos.condition_id[:16]}...\n"
                    f"USDC.e recovered to proxy wallet"
                )
            else:
                log.warning("AUTO-REDEEM returned None for %s", pos.condition_id[:16])
        except Exception as exc:
            log.error("AUTO-REDEEM error for %s: %s", pos.condition_id[:16], exc)
            await send_alert(
                f"Auto-Claim Failed\n"
                f"Market: {pos.team}\n"
                f"Error: {exc}\n"
                f"Claim manually on polymarket.com"
            )

    def _save_state(self) -> None:
        if self._state_store:
            try:
                self._state_store.save(self._risk)
            except Exception as exc:
                log.error("Failed to save state: %s", exc)
