from __future__ import annotations

import os

import pytest


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
        assert cfg.max_buy_price == 0.85
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
