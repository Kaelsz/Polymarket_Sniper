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

POSITION_MONITOR_INTERVAL: float = 10.0
RESOLUTION_WIN_THRESHOLD: float = 1.0
RESOLUTION_LOSS_THRESHOLD: float = 0.01
STALE_CYCLES_THRESHOLD: int = 60


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
        self._stale_counts: dict[str, int] = {}

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
            shares_to_buy = amount / ask
            log.info(
                "SIZING  balance=$%.2f, slots=%d/%d → bet=$%.2f (%.4f shares)",
                balance, open_pos, max_pos, amount, shares_to_buy,
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
                self._reject(opp.token_id)
                return

            balance_before = await polymarket.get_balance_usdc()
            try:
                result = await polymarket.market_buy(opp.token_id, shares_to_buy, price=ask)
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

            # Compute actual fill from balance delta to handle partial fills
            balance_after = await polymarket.get_balance_usdc()
            actual_usdc = round(balance_before - balance_after, 4)
            if actual_usdc <= 0:
                log.warning(
                    "ORDER may not have filled (balance delta=%.4f) — using intended size",
                    actual_usdc,
                )
                actual_usdc = amount
            actual_shares = actual_usdc / ask
            if abs(actual_usdc - amount) > 1.0:
                log.warning(
                    "PARTIAL FILL detected: intended=$%.2f actual=$%.2f (%.1f%% filled)",
                    amount, actual_usdc, 100 * actual_usdc / amount,
                )

            self._risk.record_trade(
                token_id=opp.token_id,
                game="market",
                team=opp.outcome,
                match_id=opp.condition_id,
                amount_usdc=actual_usdc,
                buy_price=ask,
                shares=actual_shares,
                condition_id=opp.condition_id,
            )

        self._save_state()

        latency_ms = (time.perf_counter() - t0) * 1000
        trade_info = {
            "game": "market",
            "team": opp.outcome,
            "market": opp.question,
            "ask_price": ask,
            "amount": actual_usdc,
            "latency_ms": round(latency_ms, 1),
            "dry_run": settings.trading.dry_run,
            "result": result,
            "open_positions": self._risk.open_positions,
            "total_exposure": self._risk.total_exposure,
        }
        self._trades.append(trade_info)

        mode = "DRY RUN" if settings.trading.dry_run else "LIVE"
        fill_note = f" ⚠️ partial fill" if actual_usdc < amount * 0.9 else ""
        msg = (
            f"[{mode}] Trade executed\n"
            f"Market: {opp.question}\n"
            f"Outcome: {opp.outcome}\n"
            f"Ask: ${ask:.4f}\n"
            f"Size: ${actual_usdc:.2f}{fill_note}\n"
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
                        won = outcome.lower() == pos.team.lower()
                        resolution_price = 1.0 if won else 0.0
                        pnl = self._risk.close_position_with_pnl(
                            pos.token_id, resolution_price, source="resolution",
                        )
                        source = "API"

                if pnl is None:
                    try:
                        bid = await polymarket.best_bid(pos.token_id)
                    except Exception as exc:
                        self._stale_counts[pos.token_id] = self._stale_counts.get(pos.token_id, 0) + 1
                        count = self._stale_counts[pos.token_id]
                        log.debug(
                            "Position %s: order book error (%d/%d): %s",
                            pos.team, count, STALE_CYCLES_THRESHOLD, exc,
                        )
                        if count >= STALE_CYCLES_THRESHOLD:
                            log.warning(
                                "STALE  Removing ghost position %s (%s) after %d consecutive errors",
                                pos.team, pos.token_id[:16], count,
                            )
                            pnl = self._risk.close_position_with_pnl(
                                pos.token_id, pos.buy_price, source="stale-removed",
                            )
                            source = "stale"
                            self._stale_counts.pop(pos.token_id, None)
                        else:
                            continue
                        bid = None

                    if bid is not None:
                        self._stale_counts.pop(pos.token_id, None)

                    exit_threshold = settings.trading.exit_sell_threshold
                    if bid is not None and bid >= exit_threshold:
                        pnl = await self._execute_quick_exit(pos, bid)
                        if pnl is not None:
                            source = "quick-exit"

                    elif bid is not None and self._should_stop_loss(pos, bid):
                        pnl = await self._execute_stop_loss(pos, bid)
                        source = "stop-loss"

                if pnl is not None:
                    changed = True
                    if source == "API" and pnl > 0 and pos.condition_id and self._claimer:
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

    async def _check_resolved_fallback(self, pos: PositionRecord) -> float | None:
        """Re-check resolution when a sell fails — market may have resolved on-chain."""
        if not pos.condition_id:
            return None
        try:
            outcome = await polymarket.get_market_resolution(pos.condition_id)
            if outcome is not None:
                won = outcome.lower() == pos.team.lower()
                resolution_price = 1.0 if won else 0.0
                log.info(
                    "SELL FAILED → market already resolved: %s (%s)",
                    outcome, "WIN" if won else "LOSS",
                )
                pnl = self._risk.close_position_with_pnl(
                    pos.token_id, resolution_price, source="resolution",
                )
                if pnl is not None and won and self._claimer:
                    await self._auto_redeem(pos)
                if pnl is not None:
                    await self._alert_position_closed(pos, pnl, "resolution")
                return pnl
        except Exception as exc:
            log.debug("Resolution fallback check failed: %s", exc)
        return None

    async def _execute_quick_exit(self, pos: PositionRecord, bid_price: float) -> float | None:
        """Sell position at market when bid >= exit threshold. Capital recycled instantly."""
        shares = pos.shares if pos.shares > 0 else (pos.amount_usdc / pos.buy_price)
        log.info(
            "QUICK-EXIT  %s | bid=$%.4f (bought@$%.4f) | selling %.2f shares",
            pos.team, bid_price, pos.buy_price, shares,
        )
        try:
            await polymarket.market_sell(pos.token_id, shares)
        except Exception as exc:
            err = str(exc).lower()
            if "not enough balance / allowance" in err:
                resolved_pnl = await self._check_resolved_fallback(pos)
                if resolved_pnl is not None:
                    return resolved_pnl
                if pos.shares <= 0:
                    fallback_shares = pos.amount_usdc
                    log.warning(
                        "QUICK-EXIT retry with legacy shares fallback: %.2f",
                        fallback_shares,
                    )
                    try:
                        await polymarket.market_sell(pos.token_id, fallback_shares)
                        pos.shares = fallback_shares
                    except Exception:
                        pass
                    else:
                        return self._risk.close_position_with_pnl(
                            pos.token_id, bid_price, apply_fees=False, source="quick-exit",
                        )
                # Can't sell and not resolved yet — leave in state, retry next cycle
                log.warning(
                    "QUICK-EXIT sell blocked: %s | market not resolved yet — will retry",
                    pos.team,
                )
                await send_alert(
                    f"Quick-Exit Blocked (will retry)\n"
                    f"Market: {pos.team}\n"
                    f"Bought@${pos.buy_price:.4f} | shares={pos.shares:.2f}\n"
                    f"Sell failed, market not yet resolved. Retrying next cycle."
                )
                return None
            log.error("QUICK-EXIT SELL FAILED: %s: %s", pos.team, exc)
            await send_alert(
                f"Quick-Exit Sell Failed\n"
                f"Market: {pos.team}\n"
                f"Error: {exc}"
            )
            return None
        return self._risk.close_position_with_pnl(
            pos.token_id, bid_price, apply_fees=False, source="quick-exit",
        )

    async def _execute_stop_loss(self, pos: PositionRecord, exit_price: float) -> float | None:
        shares = pos.shares if pos.shares > 0 else (pos.amount_usdc / pos.buy_price)
        try:
            await polymarket.market_sell(pos.token_id, shares)
        except Exception as exc:
            err = str(exc).lower()
            if "not enough balance / allowance" in err:
                resolved_pnl = await self._check_resolved_fallback(pos)
                if resolved_pnl is not None:
                    return resolved_pnl
                if pos.shares <= 0:
                    fallback_shares = pos.amount_usdc
                    log.warning(
                        "STOP-LOSS retry with legacy shares fallback: %.2f",
                        fallback_shares,
                    )
                    try:
                        await polymarket.market_sell(pos.token_id, fallback_shares)
                        pos.shares = fallback_shares
                    except Exception:
                        pass
                    else:
                        return self._risk.close_position_with_pnl(
                            pos.token_id, exit_price, apply_fees=False, source="stop-loss",
                        )
                # Can't sell and not resolved yet — leave in state, retry next cycle
                log.warning(
                    "STOP-LOSS sell blocked: %s | market not resolved yet — will retry",
                    pos.team,
                )
                return None
            log.error("STOP-LOSS SELL FAILED: %s: %s", pos.team, exc)
            await send_alert(
                f"Stop-Loss Sell Failed\n"
                f"Market: {pos.team}\n"
                f"Error: {exc}"
            )
            return None
        return self._risk.close_position_with_pnl(pos.token_id, exit_price, apply_fees=False, source="stop-loss")

    async def _alert_position_closed(
        self, pos: PositionRecord, pnl: float, source: str,
    ) -> None:
        if source == "quick-exit":
            log.info("QUICK-EXIT  %s | PnL=$%.4f", pos.team, pnl)
            closed = next(
                (c for c in reversed(self._risk._closed_positions) if c.token_id == pos.token_id),
                None,
            )
            exit_p = f"${closed.exit_price:.4f}" if closed else "market"
            await send_alert(
                f"Quick-Exit SOLD\n"
                f"Market: {pos.team}\n"
                f"Bought@${pos.buy_price:.4f} → Sold@{exit_p}\n"
                f"PnL: ${pnl:+.4f}\n"
                f"Capital recycled instantly"
            )
        elif source == "stop-loss":
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
                await polymarket.refresh_balance()
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
