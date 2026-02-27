"""
Backtest Runner — replays a scenario through the risk engine.

Processes events sequentially, applies risk checks, simulates trades
at the scenario's ask prices, and resolves positions to compute
realized PnL. No network calls are made.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from backtest.report import BacktestReport
from backtest.scenario import Scenario, ScenarioEvent
from core.risk import RiskConfig, RiskManager
from core.sizing import OrderSizer, SizingConfig

log = logging.getLogger("polysniper.backtest.runner")


@dataclass
class TradeRecord:
    """Record of a single trade executed during backtesting."""

    event: ScenarioEvent
    amount_usdc: float
    buy_price: float
    resolution: str = ""
    exit_price: float = 0.0
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    vetoed: bool = False
    veto_reason: str = ""


class BacktestRunner:
    """Replays a scenario and collects trade statistics."""

    def __init__(self, scenario: Scenario) -> None:
        self._scenario = scenario
        self._trades: list[TradeRecord] = []

    def run(self) -> BacktestReport:
        """Execute the backtest and return a report."""
        t0 = time.perf_counter()
        sc = self._scenario

        cfg = RiskConfig(
            fee_rate=sc.fee_rate if sc.fee_rate is not None else 0.02,
            stop_loss_pct=sc.stop_loss_pct if sc.stop_loss_pct is not None else 0.0,
        )
        min_buy = sc.min_buy_price if sc.min_buy_price is not None else 0.0
        max_buy = sc.max_buy_price if sc.max_buy_price is not None else 0.99
        base_size = sc.order_size_usdc if sc.order_size_usdc is not None else 50.0

        sizer = OrderSizer(SizingConfig(
            mode=sc.sizing_mode or "fixed",
            base_size=base_size,
            min_order=sc.min_order_usdc if sc.min_order_usdc is not None else 10.0,
            max_order=sc.max_order_usdc if sc.max_order_usdc is not None else 200.0,
        ))

        risk = RiskManager(cfg)
        self._trades.clear()

        for event in sc.events:
            order_size = sizer.compute(
                fuzzy_score=event.fuzzy_score,
                ask_price=event.ask_price,
                max_buy_price=max_buy,
            )
            record = TradeRecord(event=event, amount_usdc=order_size, buy_price=event.ask_price)

            if event.ask_price < min_buy:
                record.vetoed = True
                record.veto_reason = (
                    f"ask ${event.ask_price:.3f} < min ${min_buy:.2f}"
                )
                self._trades.append(record)
                continue

            if event.ask_price > max_buy:
                record.vetoed = True
                record.veto_reason = (
                    f"ask ${event.ask_price:.3f} > max ${max_buy:.2f}"
                )
                self._trades.append(record)
                continue

            decision = risk.pre_trade_check(
                token_id=event.token_id,
                game=event.game,
                team=event.team_won,
                match_id=event.match_id,
                amount_usdc=order_size,
                ask_price=event.ask_price,
            )
            if not decision:
                record.vetoed = True
                record.veto_reason = decision.reason
                self._trades.append(record)
                continue

            risk.record_trade(
                token_id=event.token_id,
                game=event.game,
                team=event.team_won,
                match_id=event.match_id,
                amount_usdc=order_size,
                buy_price=event.ask_price,
            )

            if event.resolution in ("win", "loss"):
                exit_price = 1.0 if event.resolution == "win" else 0.0
                pnl = risk.close_position_with_pnl(event.token_id, exit_price)
                if pnl is not None:
                    shares = order_size / event.ask_price
                    gross = shares * exit_price - order_size
                    fees = 0.0
                    if gross > 0 and cfg.fee_rate > 0:
                        fees = gross * cfg.fee_rate
                    record.resolution = event.resolution
                    record.exit_price = exit_price
                    record.gross_pnl = round(gross, 4)
                    record.fees = round(fees, 4)
                    record.net_pnl = round(pnl, 4)

            self._trades.append(record)

        elapsed_ms = (time.perf_counter() - t0) * 1000

        return BacktestReport.from_trades(
            scenario_name=sc.name,
            trades=self._trades,
            final_pnl=risk.session_pnl,
            open_positions=risk.open_positions,
            elapsed_ms=elapsed_ms,
        )
