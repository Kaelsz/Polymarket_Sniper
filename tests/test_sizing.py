"""Tests for the dynamic order sizing module."""

from __future__ import annotations

import pytest

from core.sizing import OrderSizer, SizingConfig


# ------------------------------------------------------------------
# Fixed mode
# ------------------------------------------------------------------
class TestFixedMode:
    def test_returns_base_size(self):
        sizer = OrderSizer(SizingConfig(mode="fixed", base_size=50.0))
        assert sizer.compute() == 50.0

    def test_ignores_score_and_price(self):
        sizer = OrderSizer(SizingConfig(mode="fixed", base_size=100.0))
        assert sizer.compute(fuzzy_score=50.0, ask_price=0.30) == 100.0

    def test_clamped_to_max(self):
        sizer = OrderSizer(SizingConfig(mode="fixed", base_size=300.0, max_order=200.0))
        assert sizer.compute() == 200.0

    def test_clamped_to_min(self):
        sizer = OrderSizer(SizingConfig(mode="fixed", base_size=5.0, min_order=10.0))
        assert sizer.compute() == 10.0


# ------------------------------------------------------------------
# Confidence mode
# ------------------------------------------------------------------
class TestConfidenceMode:
    def test_high_confidence_increases_size(self):
        sizer = OrderSizer(SizingConfig(mode="confidence", base_size=100.0, max_order=500.0))
        high = sizer.compute(fuzzy_score=100.0, ask_price=0.40, max_buy_price=0.85)
        low = sizer.compute(fuzzy_score=65.0, ask_price=0.80, max_buy_price=0.85)
        assert high > low

    def test_perfect_score_max_edge(self):
        sizer = OrderSizer(SizingConfig(mode="confidence", base_size=100.0, max_order=500.0))
        size = sizer.compute(fuzzy_score=100.0, ask_price=0.01, max_buy_price=0.85)
        assert size == pytest.approx(150.0, abs=1.0)

    def test_low_score_no_edge(self):
        sizer = OrderSizer(SizingConfig(mode="confidence", base_size=100.0))
        size = sizer.compute(fuzzy_score=0.0, ask_price=0.85, max_buy_price=0.85)
        assert size == pytest.approx(50.0, abs=1.0)

    def test_mid_range(self):
        sizer = OrderSizer(SizingConfig(mode="confidence", base_size=100.0, max_order=500.0))
        size = sizer.compute(fuzzy_score=80.0, ask_price=0.60, max_buy_price=0.85)
        assert 80.0 < size < 140.0

    def test_respects_min_max(self):
        sizer = OrderSizer(SizingConfig(
            mode="confidence", base_size=100.0,
            min_order=60.0, max_order=130.0,
        ))
        low = sizer.compute(fuzzy_score=0.0, ask_price=0.85, max_buy_price=0.85)
        high = sizer.compute(fuzzy_score=100.0, ask_price=0.01, max_buy_price=0.85)
        assert low >= 60.0
        assert high <= 130.0

    def test_custom_weights(self):
        edge_heavy = OrderSizer(SizingConfig(
            mode="confidence", base_size=100.0, max_order=500.0,
            confidence_score_weight=0.0, confidence_edge_weight=1.0,
        ))
        score_heavy = OrderSizer(SizingConfig(
            mode="confidence", base_size=100.0, max_order=500.0,
            confidence_score_weight=1.0, confidence_edge_weight=0.0,
        ))
        e = edge_heavy.compute(fuzzy_score=65.0, ask_price=0.30, max_buy_price=0.85)
        s = score_heavy.compute(fuzzy_score=65.0, ask_price=0.30, max_buy_price=0.85)
        assert e != s


# ------------------------------------------------------------------
# Kelly mode
# ------------------------------------------------------------------
class TestKellyMode:
    def test_low_price_high_edge(self):
        sizer = OrderSizer(SizingConfig(
            mode="kelly", base_size=100.0, max_order=500.0,
            kelly_fraction=0.25, kelly_win_prob=0.90,
        ))
        size = sizer.compute(ask_price=0.40)
        assert size > 50.0

    def test_high_price_low_edge(self):
        sizer = OrderSizer(SizingConfig(
            mode="kelly", base_size=100.0,
            kelly_fraction=0.25, kelly_win_prob=0.90,
        ))
        size = sizer.compute(ask_price=0.85)
        assert size < 100.0

    def test_no_edge_returns_min(self):
        sizer = OrderSizer(SizingConfig(
            mode="kelly", base_size=100.0, min_order=10.0,
            kelly_fraction=0.25, kelly_win_prob=0.50,
        ))
        size = sizer.compute(ask_price=0.85)
        assert size == 10.0

    def test_price_at_boundary_zero(self):
        sizer = OrderSizer(SizingConfig(mode="kelly", base_size=100.0))
        size = sizer.compute(ask_price=0.0)
        assert size == 100.0

    def test_price_at_boundary_one(self):
        sizer = OrderSizer(SizingConfig(mode="kelly", base_size=100.0))
        size = sizer.compute(ask_price=1.0)
        assert size == 100.0

    def test_full_kelly_fraction(self):
        quarter = OrderSizer(SizingConfig(
            mode="kelly", base_size=100.0, max_order=500.0,
            kelly_fraction=0.25, kelly_win_prob=0.90,
        ))
        half = OrderSizer(SizingConfig(
            mode="kelly", base_size=100.0, max_order=500.0,
            kelly_fraction=0.50, kelly_win_prob=0.90,
        ))
        q_size = quarter.compute(ask_price=0.50)
        h_size = half.compute(ask_price=0.50)
        assert h_size > q_size

    def test_respects_clamp(self):
        sizer = OrderSizer(SizingConfig(
            mode="kelly", base_size=100.0,
            min_order=20.0, max_order=80.0,
            kelly_fraction=1.0, kelly_win_prob=0.95,
        ))
        size = sizer.compute(ask_price=0.20)
        assert size <= 80.0


# ------------------------------------------------------------------
# Default sizer
# ------------------------------------------------------------------
class TestDefaultSizer:
    def test_default_is_fixed(self):
        sizer = OrderSizer()
        assert sizer.mode == "fixed"

    def test_default_base_50(self):
        sizer = OrderSizer()
        assert sizer.compute() == 50.0


# ------------------------------------------------------------------
# Config validation integration
# ------------------------------------------------------------------
class TestSizingConfigValidation:
    def test_invalid_mode_caught(self):
        from core.config import PolymarketConfig, Settings, TelegramConfig, TradingConfig, validate_config

        s = Settings(
            poly=PolymarketConfig(
                address="0x69d9A1Ec63A45139DdAc8d2347dCF752C6BF3041",
                private_key="0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            ),
            telegram=TelegramConfig(),
            trading=TradingConfig(sizing_mode="invalid"),
        )
        errs = validate_config(s)
        assert any("SIZING_MODE" in e for e in errs)

    def test_min_exceeds_max_caught(self):
        from core.config import PolymarketConfig, Settings, TelegramConfig, TradingConfig, validate_config

        s = Settings(
            poly=PolymarketConfig(
                address="0x69d9A1Ec63A45139DdAc8d2347dCF752C6BF3041",
                private_key="0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            ),
            telegram=TelegramConfig(),
            trading=TradingConfig(min_order_usdc=300.0, max_order_usdc=100.0),
        )
        errs = validate_config(s)
        assert any("cannot exceed" in e for e in errs)
