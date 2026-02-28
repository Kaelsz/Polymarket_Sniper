"""
Risk Management Module

Protections:
  - Duplicate trade prevention (same match/token)
  - Per-session position limits (max open positions, max per game)
  - Session loss circuit breaker (halt if cumulative loss exceeds threshold)
  - Per-match cooldown (avoid rapid-fire on the same match)
  - Max exposure cap (total USDC committed)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("polysniper.risk")


@dataclass(slots=True)
class RiskConfig:
    max_open_positions: int = 10
    max_positions_per_game: int = 4
    max_session_loss_usdc: float = 200.0
    max_total_exposure_usdc: float = 500.0
    match_cooldown_seconds: float = 30.0
    dedup_window_seconds: float = 300.0
    fee_rate: float = 0.0
    stop_loss_pct: float = 0.0


class RiskVeto:
    """Returned when the RiskManager blocks a trade."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"RiskVeto({self.reason!r})"


class RiskClear:
    """Returned when the RiskManager approves a trade."""

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        return "RiskClear()"


RiskDecision = RiskVeto | RiskClear


@dataclass
class PositionRecord:
    token_id: str
    game: str
    team: str
    match_id: str
    amount_usdc: float
    buy_price: float
    condition_id: str = ""
    timestamp: float = field(default_factory=time.time)


class RiskManager:
    def __init__(self, config: RiskConfig | None = None) -> None:
        self._cfg = config or RiskConfig()
        self._positions: list[PositionRecord] = []
        self._trade_keys: dict[str, float] = {}
        self._match_cooldowns: dict[str, float] = {}
        self._session_pnl: float = 0.0
        self._halted: bool = False
        self._halt_reason: str = ""

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    @property
    def session_pnl(self) -> float:
        return self._session_pnl

    @property
    def open_positions(self) -> int:
        return len(self._positions)

    @property
    def total_exposure(self) -> float:
        return sum(p.amount_usdc for p in self._positions)

    def pre_trade_check(
        self,
        token_id: str,
        game: str,
        team: str,
        match_id: str,
        amount_usdc: float,
        ask_price: float,
    ) -> RiskDecision:
        """
        Run all pre-trade risk checks. Returns RiskClear or RiskVeto.
        """
        if self._halted:
            return RiskVeto(f"Trading halted: {self._halt_reason}")

        if reason := self._check_dedup(token_id, match_id, team):
            return RiskVeto(reason)

        if reason := self._check_cooldown(match_id):
            return RiskVeto(reason)

        if reason := self._check_position_limits(game):
            return RiskVeto(reason)

        if reason := self._check_exposure(amount_usdc):
            return RiskVeto(reason)

        if reason := self._check_session_loss():
            return RiskVeto(reason)

        return RiskClear()

    def record_trade(
        self,
        token_id: str,
        game: str,
        team: str,
        match_id: str,
        amount_usdc: float,
        buy_price: float,
        condition_id: str = "",
    ) -> None:
        """Record a successfully executed trade."""
        now = time.time()
        self._positions.append(PositionRecord(
            token_id=token_id,
            game=game,
            team=team,
            match_id=match_id,
            amount_usdc=amount_usdc,
            buy_price=buy_price,
            condition_id=condition_id,
            timestamp=now,
        ))

        dedup_key = self._dedup_key(token_id, match_id, team)
        self._trade_keys[dedup_key] = now
        self._match_cooldowns[match_id] = now

        log.info(
            "RISK  Position recorded: %s %s $%.2f @ $%.3f | open=%d exposure=$%.2f",
            game, team, amount_usdc, buy_price,
            self.open_positions, self.total_exposure,
        )

    def record_pnl(self, realized_pnl: float) -> None:
        """Update session PnL (negative = loss). May trigger halt."""
        self._session_pnl += realized_pnl
        log.info("RISK  Session PnL: $%.2f (delta: $%.2f)", self._session_pnl, realized_pnl)

        if self._session_pnl <= -self._cfg.max_session_loss_usdc:
            self._halt("Session loss limit hit: $%.2f" % self._session_pnl)

    def close_position(self, token_id: str) -> None:
        """Remove a position by token_id (e.g. after market resolves)."""
        self._positions = [p for p in self._positions if p.token_id != token_id]

    def close_position_with_pnl(
        self,
        token_id: str,
        exit_price: float,
        *,
        apply_fees: bool = True,
    ) -> float | None:
        """
        Close a position and record realized PnL.

        For Polymarket binary outcomes:
          exit_price = 1.0  → YES wins  (resolution)
          exit_price = 0.0  → YES loses (resolution)
          exit_price = X    → early exit (stop-loss / manual sell)

        Fees (configured via fee_rate) are deducted from positive PnL only,
        and only when apply_fees=True (default). Stop-loss exits should
        pass apply_fees=False since fees only apply on resolution winnings.

        Returns the net realized PnL, or None if position not found.
        """
        pos = next((p for p in self._positions if p.token_id == token_id), None)
        if pos is None:
            return None

        shares = pos.amount_usdc / pos.buy_price
        gross_pnl = shares * exit_price - pos.amount_usdc

        fees = 0.0
        if apply_fees and gross_pnl > 0 and self._cfg.fee_rate > 0:
            fees = gross_pnl * self._cfg.fee_rate

        pnl = gross_pnl - fees

        log.info(
            "RISK  Position closed: %s %s | bought@$%.3f exit@$%.3f | "
            "shares=%.2f gross=$%.2f fees=$%.2f net=$%.2f",
            pos.game, pos.team, pos.buy_price, exit_price,
            shares, gross_pnl, fees, pnl,
        )

        self.close_position(token_id)
        self.record_pnl(pnl)
        return pnl

    def halt(self, reason: str) -> None:
        """Externally triggered halt (e.g. from circuit breaker)."""
        self._halt(reason)

    def resume(self) -> None:
        """Resume trading after a halt."""
        if self._halted:
            log.warning("RISK  Trading resumed (was: %s)", self._halt_reason)
            self._halted = False
            self._halt_reason = ""

    def _halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason
        log.critical("RISK  TRADING HALTED: %s", reason)

    def _check_dedup(self, token_id: str, match_id: str, team: str) -> str:
        if any(p.token_id == token_id for p in self._positions):
            return f"Already holding position on token {token_id[:16]}"

        if any(p.match_id == match_id for p in self._positions):
            return f"Already holding position on market {match_id[:16]}"

        key = self._dedup_key(token_id, match_id, team)
        last_trade = self._trade_keys.get(key)
        if last_trade is None:
            return ""
        elapsed = time.time() - last_trade
        if elapsed < self._cfg.dedup_window_seconds:
            return f"Duplicate trade blocked ({key}, {elapsed:.0f}s ago)"
        return ""

    def _check_cooldown(self, match_id: str) -> str:
        if not match_id:
            return ""
        last_trade = self._match_cooldowns.get(match_id)
        if last_trade is None:
            return ""
        elapsed = time.time() - last_trade
        if elapsed < self._cfg.match_cooldown_seconds:
            return f"Match cooldown active ({match_id}, {elapsed:.1f}s < {self._cfg.match_cooldown_seconds}s)"
        return ""

    def _check_position_limits(self, game: str) -> str:
        if self.open_positions >= self._cfg.max_open_positions:
            return f"Max open positions reached ({self.open_positions}/{self._cfg.max_open_positions})"
        game_positions = sum(1 for p in self._positions if p.game == game)
        if game_positions >= self._cfg.max_positions_per_game:
            return f"Max positions for {game} reached ({game_positions}/{self._cfg.max_positions_per_game})"
        return ""

    def _check_exposure(self, amount_usdc: float) -> str:
        projected = self.total_exposure + amount_usdc
        if projected > self._cfg.max_total_exposure_usdc:
            return f"Exposure cap exceeded (${projected:.2f} > ${self._cfg.max_total_exposure_usdc:.2f})"
        return ""

    def _check_session_loss(self) -> str:
        if self._session_pnl <= -self._cfg.max_session_loss_usdc:
            return f"Session loss limit (PnL: ${self._session_pnl:.2f})"
        return ""

    # ------------------------------------------------------------------
    # Serialization — save/load state for persistence
    # ------------------------------------------------------------------
    def to_state_dict(self) -> dict:
        return {
            "version": 1,
            "timestamp": time.time(),
            "session_pnl": self._session_pnl,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "positions": [
                {
                    "token_id": p.token_id,
                    "game": p.game,
                    "team": p.team,
                    "match_id": p.match_id,
                    "amount_usdc": p.amount_usdc,
                    "buy_price": p.buy_price,
                    "condition_id": p.condition_id,
                    "timestamp": p.timestamp,
                }
                for p in self._positions
            ],
            "trade_keys": dict(self._trade_keys),
            "match_cooldowns": dict(self._match_cooldowns),
        }

    def load_state_dict(self, state: dict) -> None:
        self._session_pnl = state.get("session_pnl", 0.0)

        saved_halted = state.get("halted", False)
        saved_reason = state.get("halt_reason", "")
        if saved_halted and "circuit breaker" in saved_reason.lower():
            self._halted = False
            self._halt_reason = ""
            log.info("RISK  Ignoring persisted CB halt on restart (CB resets on boot)")
        else:
            self._halted = saved_halted
            self._halt_reason = saved_reason

        self._positions = [
            PositionRecord(**p) for p in state.get("positions", [])
        ]
        self._trade_keys = dict(state.get("trade_keys", {}))
        self._match_cooldowns = dict(state.get("match_cooldowns", {}))
        log.info(
            "RISK  State loaded: %d positions, PnL=$%.2f, halted=%s",
            self.open_positions, self._session_pnl, self._halted,
        )

    @staticmethod
    def _dedup_key(token_id: str, match_id: str, team: str) -> str:
        return f"{token_id}|{match_id}|{team}".lower()
