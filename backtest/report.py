"""
Backtest Report — statistics and formatted output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backtest.runner import TradeRecord


@dataclass
class BacktestReport:
    scenario_name: str = ""
    total_events: int = 0
    executed_trades: int = 0
    vetoed_trades: int = 0
    wins: int = 0
    losses: int = 0
    unresolved: int = 0
    total_pnl: float = 0.0
    gross_pnl: float = 0.0
    total_fees: float = 0.0
    max_drawdown: float = 0.0
    peak_pnl: float = 0.0
    total_invested: float = 0.0
    open_positions: int = 0
    elapsed_ms: float = 0.0
    per_game: dict[str, dict] = field(default_factory=dict)
    veto_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def win_rate(self) -> float:
        resolved = self.wins + self.losses
        return (self.wins / resolved * 100) if resolved > 0 else 0.0

    @property
    def avg_win(self) -> float:
        return (self.gross_pnl / self.wins) if self.wins > 0 else 0.0

    @property
    def roi_pct(self) -> float:
        return (self.total_pnl / self.total_invested * 100) if self.total_invested > 0 else 0.0

    @classmethod
    def from_trades(
        cls,
        scenario_name: str,
        trades: list[TradeRecord],
        final_pnl: float,
        open_positions: int,
        elapsed_ms: float,
    ) -> BacktestReport:
        report = cls(
            scenario_name=scenario_name,
            total_events=len(trades),
            total_pnl=round(final_pnl, 2),
            open_positions=open_positions,
            elapsed_ms=round(elapsed_ms, 2),
        )

        cumulative_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        game_stats: dict[str, dict] = {}

        for t in trades:
            game = t.event.game
            if game not in game_stats:
                game_stats[game] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}

            if t.vetoed:
                report.vetoed_trades += 1
                reason_key = t.veto_reason.split("(")[0].strip()
                report.veto_reasons[reason_key] = report.veto_reasons.get(reason_key, 0) + 1
                continue

            report.executed_trades += 1
            report.total_invested += t.amount_usdc
            game_stats[game]["trades"] += 1

            if t.resolution == "win":
                report.wins += 1
                report.gross_pnl += t.gross_pnl
                report.total_fees += t.fees
                game_stats[game]["wins"] += 1
                game_stats[game]["pnl"] += t.net_pnl
                cumulative_pnl += t.net_pnl
            elif t.resolution == "loss":
                report.losses += 1
                report.gross_pnl += t.gross_pnl
                game_stats[game]["losses"] += 1
                game_stats[game]["pnl"] += t.net_pnl
                cumulative_pnl += t.net_pnl
            else:
                report.unresolved += 1

            if cumulative_pnl > peak:
                peak = cumulative_pnl
            dd = peak - cumulative_pnl
            if dd > max_dd:
                max_dd = dd

        report.gross_pnl = round(report.gross_pnl, 2)
        report.total_fees = round(report.total_fees, 2)
        report.peak_pnl = round(peak, 2)
        report.max_drawdown = round(max_dd, 2)
        report.total_invested = round(report.total_invested, 2)
        report.per_game = {g: {k: round(v, 2) if isinstance(v, float) else v for k, v in s.items()} for g, s in game_stats.items()}

        return report

    def summary(self) -> str:
        resolved = self.wins + self.losses
        lines = [
            "",
            "=" * 60,
            f"  BACKTEST REPORT — {self.scenario_name}",
            "=" * 60,
            "",
            f"  Events:        {self.total_events}",
            f"  Executed:      {self.executed_trades}",
            f"  Vetoed:        {self.vetoed_trades}",
            f"  Resolved:      {resolved}  (W:{self.wins} / L:{self.losses})",
            f"  Unresolved:    {self.unresolved}",
            f"  Open:          {self.open_positions}",
            "",
            f"  Win Rate:      {self.win_rate:.1f}%",
            f"  ROI:           {self.roi_pct:+.1f}%",
            "",
            f"  Gross PnL:     ${self.gross_pnl:+.2f}",
            f"  Fees Paid:     ${self.total_fees:.2f}",
            f"  Net PnL:       ${self.total_pnl:+.2f}",
            f"  Peak PnL:      ${self.peak_pnl:+.2f}",
            f"  Max Drawdown:  ${self.max_drawdown:.2f}",
            f"  Invested:      ${self.total_invested:.2f}",
            "",
        ]

        if self.per_game:
            lines.append("  Per Game:")
            for game, stats in sorted(self.per_game.items()):
                wr = (stats["wins"] / (stats["wins"] + stats["losses"]) * 100) if (stats["wins"] + stats["losses"]) > 0 else 0
                lines.append(
                    f"    {game:12s}  trades={stats['trades']}  "
                    f"W={stats['wins']} L={stats['losses']}  "
                    f"WR={wr:.0f}%  PnL=${stats['pnl']:+.2f}"
                )
            lines.append("")

        if self.veto_reasons:
            lines.append("  Veto Reasons:")
            for reason, count in sorted(self.veto_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"    {count:3d}x  {reason}")
            lines.append("")

        lines.append(f"  Backtest ran in {self.elapsed_ms:.1f}ms")
        lines.append("=" * 60)
        return "\n".join(lines)
