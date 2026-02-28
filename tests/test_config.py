from __future__ import annotations

import os

import pytest

from core.config import (
    ConfigError,
    PolymarketConfig,
    Settings,
    TelegramConfig,
    TradingConfig,
    validate_config,
)


def _valid_settings(**overrides) -> Settings:
    """Build a Settings object with valid defaults, applying overrides."""
    poly_kw = {
        "address": "0x69d9A1Ec63A45139DdAc8d2347dCF752C6BF3041",
        "private_key": "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        "host": "https://clob.polymarket.com",
    }
    trading_kw = {
        "min_buy_price": 0.0,
        "max_buy_price": 0.85,
        "scanner_interval": 30.0,
        "min_volume_usdc": 100000.0,
        "max_end_hours": 2.0,
        "order_size_usdc": 50.0,
        "dry_run": True,
        "max_open_positions": 10,
        "max_positions_per_game": 4,
        "max_session_loss_usdc": 200.0,
        "max_total_exposure_usdc": 500.0,
        "match_cooldown_seconds": 30.0,
        "cb_failure_threshold": 3,
        "cb_min_healthy_adapters": 1,
        "cb_stale_data_timeout": 120.0,
        "fee_rate": 0.02,
        "stop_loss_pct": 0.0,
        "dashboard_port": 8080,
        "api_rate_limit": 5.0,
        "api_rate_burst": 10,
    }
    for k, v in overrides.items():
        if k in poly_kw:
            poly_kw[k] = v
        elif k in trading_kw:
            trading_kw[k] = v
    return Settings(
        poly=PolymarketConfig(**poly_kw),
        telegram=TelegramConfig(),
        trading=TradingConfig(**trading_kw),
    )


class TestPolymarketConfig:
    def test_loads_from_env(self):
        from core.config import PolymarketConfig

        cfg = PolymarketConfig()
        assert cfg.address == os.environ["POLYMARKET_ADDRESS"]
        assert cfg.private_key == os.environ["POLY_PRIVATE_KEY"]

    def test_default_host(self):
        from core.config import PolymarketConfig

        cfg = PolymarketConfig()
        assert "clob.polymarket.com" in cfg.host

    def test_missing_address_raises(self, monkeypatch):
        monkeypatch.delenv("POLYMARKET_ADDRESS", raising=False)
        from core.config import _require

        with pytest.raises(EnvironmentError, match="POLYMARKET_ADDRESS"):
            _require("POLYMARKET_ADDRESS")


class TestTelegramConfig:
    def test_disabled_when_empty(self):
        from core.config import TelegramConfig

        cfg = TelegramConfig()
        assert not cfg.enabled

    def test_enabled_when_set(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        from core.config import TelegramConfig

        cfg = TelegramConfig()
        assert cfg.enabled
        assert cfg.bot_token == "fake_token"
        assert cfg.chat_id == "12345"


class TestTradingConfig:
    def test_defaults(self):
        from core.config import TradingConfig

        cfg = TradingConfig()
        assert cfg.min_buy_price == float(os.getenv("MIN_BUY_PRICE", "0.95"))
        assert cfg.max_buy_price == float(os.getenv("MAX_BUY_PRICE", "0.99"))
        assert cfg.order_size_usdc == 50.0
        assert cfg.dry_run is True

    def test_custom_values(self, monkeypatch):
        monkeypatch.setenv("MAX_BUY_PRICE", "0.90")
        monkeypatch.setenv("ORDER_SIZE_USDC", "100.0")
        monkeypatch.setenv("DRY_RUN", "false")
        from core.config import TradingConfig

        cfg = TradingConfig()
        assert cfg.max_buy_price == 0.90
        assert cfg.order_size_usdc == 100.0
        assert cfg.dry_run is False


class TestSettings:
    def test_settings_singleton(self):
        from core.config import settings

        assert settings.poly.address == os.environ["POLYMARKET_ADDRESS"]
        assert settings.trading.dry_run is True


class TestValidateConfigValid:
    def test_valid_defaults_pass(self):
        assert validate_config(_valid_settings()) == []

    def test_valid_with_stop_loss(self):
        assert validate_config(_valid_settings(stop_loss_pct=0.3)) == []

    def test_valid_edge_max_buy_price_1(self):
        assert validate_config(_valid_settings(max_buy_price=1.0)) == []

    def test_valid_min_buy_price_zero(self):
        assert validate_config(_valid_settings(min_buy_price=0.0)) == []

    def test_valid_fee_rate_zero(self):
        assert validate_config(_valid_settings(fee_rate=0.0)) == []


class TestValidateConfigAddress:
    def test_invalid_address_not_hex(self):
        errs = validate_config(_valid_settings(address="not_an_address"))
        assert any("POLYMARKET_ADDRESS" in e and "hex" in e for e in errs)

    def test_invalid_address_wrong_length(self):
        errs = validate_config(_valid_settings(address="0xTooShort"))
        assert any("42 characters" in e for e in errs)

    def test_invalid_private_key_not_hex(self):
        errs = validate_config(_valid_settings(private_key="my_secret"))
        assert any("POLY_PRIVATE_KEY" in e for e in errs)


class TestValidateConfigHost:
    def test_invalid_host_no_http(self):
        errs = validate_config(_valid_settings(host="ftp://wrong"))
        assert any("POLYMARKET_HOST" in e for e in errs)


class TestValidateConfigTrading:
    def test_max_buy_price_zero(self):
        errs = validate_config(_valid_settings(max_buy_price=0.0))
        assert any("MAX_BUY_PRICE" in e for e in errs)

    def test_max_buy_price_above_one(self):
        errs = validate_config(_valid_settings(max_buy_price=1.5))
        assert any("MAX_BUY_PRICE" in e for e in errs)

    def test_min_buy_price_negative(self):
        errs = validate_config(_valid_settings(min_buy_price=-0.1))
        assert any("MIN_BUY_PRICE" in e for e in errs)

    def test_min_exceeds_max(self):
        errs = validate_config(_valid_settings(min_buy_price=0.90, max_buy_price=0.85))
        assert any("MIN_BUY_PRICE" in e and "less than" in e for e in errs)

    def test_order_size_negative(self):
        errs = validate_config(_valid_settings(order_size_usdc=-10.0))
        assert any("ORDER_SIZE_USDC" in e for e in errs)

    def test_order_size_zero(self):
        errs = validate_config(_valid_settings(order_size_usdc=0.0))
        assert any("ORDER_SIZE_USDC" in e for e in errs)

    def test_max_open_positions_zero(self):
        errs = validate_config(_valid_settings(max_open_positions=0))
        assert any("MAX_OPEN_POSITIONS" in e for e in errs)

    def test_positions_per_game_exceeds_total(self):
        errs = validate_config(_valid_settings(
            max_open_positions=3, max_positions_per_game=5,
        ))
        assert any("cannot exceed" in e for e in errs)

    def test_session_loss_zero(self):
        errs = validate_config(_valid_settings(max_session_loss_usdc=0.0))
        assert any("MAX_SESSION_LOSS_USDC" in e for e in errs)

    def test_exposure_zero(self):
        errs = validate_config(_valid_settings(max_total_exposure_usdc=0.0))
        assert any("MAX_TOTAL_EXPOSURE_USDC" in e for e in errs)

    def test_order_size_exceeds_exposure(self):
        errs = validate_config(_valid_settings(
            order_size_usdc=100.0, max_total_exposure_usdc=50.0,
        ))
        assert any("cannot exceed" in e for e in errs)

    def test_negative_cooldown(self):
        errs = validate_config(_valid_settings(match_cooldown_seconds=-1.0))
        assert any("MATCH_COOLDOWN_SECONDS" in e for e in errs)


class TestValidateConfigFees:
    def test_fee_rate_negative(self):
        errs = validate_config(_valid_settings(fee_rate=-0.01))
        assert any("FEE_RATE" in e for e in errs)

    def test_fee_rate_above_one(self):
        errs = validate_config(_valid_settings(fee_rate=1.0))
        assert any("FEE_RATE" in e for e in errs)

    def test_stop_loss_negative(self):
        errs = validate_config(_valid_settings(stop_loss_pct=-0.1))
        assert any("STOP_LOSS_PCT" in e for e in errs)

    def test_stop_loss_above_one(self):
        errs = validate_config(_valid_settings(stop_loss_pct=1.0))
        assert any("STOP_LOSS_PCT" in e for e in errs)


class TestValidateConfigCircuitBreaker:
    def test_failure_threshold_zero(self):
        errs = validate_config(_valid_settings(cb_failure_threshold=0))
        assert any("CB_FAILURE_THRESHOLD" in e for e in errs)

    def test_min_healthy_negative(self):
        errs = validate_config(_valid_settings(cb_min_healthy_adapters=-1))
        assert any("CB_MIN_HEALTHY_ADAPTERS" in e for e in errs)

    def test_stale_timeout_zero(self):
        errs = validate_config(_valid_settings(cb_stale_data_timeout=0.0))
        assert any("CB_STALE_DATA_TIMEOUT" in e for e in errs)


class TestValidateConfigDashboard:
    def test_port_zero(self):
        errs = validate_config(_valid_settings(dashboard_port=0))
        assert any("DASHBOARD_PORT" in e for e in errs)

    def test_port_too_high(self):
        errs = validate_config(_valid_settings(dashboard_port=70000))
        assert any("DASHBOARD_PORT" in e for e in errs)

    def test_valid_port(self):
        assert validate_config(_valid_settings(dashboard_port=3000)) == []


class TestValidateConfigMultipleErrors:
    def test_accumulates_all_errors(self):
        errs = validate_config(_valid_settings(
            address="bad",
            order_size_usdc=-1,
            fee_rate=2.0,
            dashboard_port=0,
        ))
        assert len(errs) >= 4
