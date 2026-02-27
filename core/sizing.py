"""
Dynamic Order Sizing

Three strategies:
  - fixed:      Always use base_size (backward compatible)
  - confidence: Scale by fuzzy score + price edge
  - kelly:      Kelly criterion based on implied edge

All strategies clamp output to [min_order, max_order].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("polysniper.sizing")


@dataclass(frozen=True, slots=True)
class SizingConfig:
    mode: str = "fixed"          # "fixed", "confidence", "kelly"
    base_size: float = 50.0      # base order size in USDC
    min_order: float = 10.0      # floor
    max_order: float = 200.0     # ceiling
    kelly_fraction: float = 0.25  # quarter-Kelly (safer)
    kelly_win_prob: float = 0.90  # estimated P(win) for confirmed results
    confidence_score_weight: float = 0.6
    confidence_edge_weight: float = 0.4


class OrderSizer:
    """Computes dynamic order sizes based on the configured strategy."""

    def __init__(self, config: SizingConfig | None = None) -> None:
        self._cfg = config or SizingConfig()

    @property
    def mode(self) -> str:
        return self._cfg.mode

    def compute(
        self,
        fuzzy_score: float = 100.0,
        ask_price: float = 0.50,
        max_buy_price: float = 0.85,
    ) -> float:
        """
        Compute the order size in USDC.

        Args:
            fuzzy_score: Team name match score (0-100 from rapidfuzz)
            ask_price: Current ask price on the order book
            max_buy_price: Maximum acceptable buy price
        """
        cfg = self._cfg

        if cfg.mode == "confidence":
            size = self._confidence(fuzzy_score, ask_price, max_buy_price)
        elif cfg.mode == "kelly":
            size = self._kelly(ask_price)
        else:
            size = cfg.base_size

        clamped = max(cfg.min_order, min(size, cfg.max_order))

        if clamped != size:
            log.debug(
                "SIZING  %s: raw=$%.2f clamped=$%.2f (min=$%.2f max=$%.2f)",
                cfg.mode, size, clamped, cfg.min_order, cfg.max_order,
            )

        return round(clamped, 2)

    def _confidence(
        self,
        fuzzy_score: float,
        ask_price: float,
        max_buy_price: float,
    ) -> float:
        """
        Scale base_size by a confidence factor (0.5 to 1.5).

        Two signals:
          - score_factor: fuzzy_score / 100 (higher match = more confident)
          - edge_factor: how far below max_buy_price the ask is
        """
        cfg = self._cfg

        score_factor = min(fuzzy_score / 100.0, 1.0)

        if max_buy_price > 0:
            edge_factor = (max_buy_price - ask_price) / max_buy_price
        else:
            edge_factor = 0.0
        edge_factor = max(0.0, min(edge_factor, 1.0))

        w_score = cfg.confidence_score_weight
        w_edge = cfg.confidence_edge_weight
        total_weight = w_score + w_edge
        if total_weight > 0:
            combined = (w_score * score_factor + w_edge * edge_factor) / total_weight
        else:
            combined = 0.5

        multiplier = 0.5 + combined  # range [0.5, 1.5]

        return cfg.base_size * multiplier

    def _kelly(self, ask_price: float) -> float:
        """
        Kelly criterion: f* = (p*b - q) / b

        Where:
          p = estimated win probability
          q = 1 - p
          b = net odds (payout per $1 wagered) = (1/ask_price) - 1
        """
        cfg = self._cfg
        p = cfg.kelly_win_prob
        q = 1.0 - p

        if ask_price <= 0 or ask_price >= 1.0:
            return cfg.base_size

        b = (1.0 / ask_price) - 1.0
        if b <= 0:
            return cfg.min_order

        kelly_f = (p * b - q) / b
        if kelly_f <= 0:
            return cfg.min_order

        return cfg.base_size * kelly_f * cfg.kelly_fraction * 4
