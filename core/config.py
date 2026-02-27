from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_ENV_PATH)


def _require(var: str) -> str:
    val = os.getenv(var)
    if not val:
        raise EnvironmentError(f"Missing required env var: {var}")
    return val


@dataclass(frozen=True, slots=True)
class PolymarketConfig:
    address: str = field(default_factory=lambda: _require("POLYMARKET_ADDRESS"))
    private_key: str = field(default_factory=lambda: _require("POLY_PRIVATE_KEY"))
    host: str = field(
        default_factory=lambda: os.getenv(
            "POLYMARKET_HOST", "https://clob.polymarket.com"
        )
    )


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True, slots=True)
class TradingConfig:
    max_buy_price: float = field(
        default_factory=lambda: float(os.getenv("MAX_BUY_PRICE", "0.85"))
    )
    order_size_usdc: float = field(
        default_factory=lambda: float(os.getenv("ORDER_SIZE_USDC", "50.0"))
    )
    dry_run: bool = field(
        default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true"
    )
    max_open_positions: int = field(
        default_factory=lambda: int(os.getenv("MAX_OPEN_POSITIONS", "10"))
    )
    max_positions_per_game: int = field(
        default_factory=lambda: int(os.getenv("MAX_POSITIONS_PER_GAME", "4"))
    )
    max_session_loss_usdc: float = field(
        default_factory=lambda: float(os.getenv("MAX_SESSION_LOSS_USDC", "200.0"))
    )
    max_total_exposure_usdc: float = field(
        default_factory=lambda: float(os.getenv("MAX_TOTAL_EXPOSURE_USDC", "500.0"))
    )
    match_cooldown_seconds: float = field(
        default_factory=lambda: float(os.getenv("MATCH_COOLDOWN_SECONDS", "30.0"))
    )
    cb_failure_threshold: int = field(
        default_factory=lambda: int(os.getenv("CB_FAILURE_THRESHOLD", "3"))
    )
    cb_min_healthy_adapters: int = field(
        default_factory=lambda: int(os.getenv("CB_MIN_HEALTHY_ADAPTERS", "1"))
    )
    cb_stale_data_timeout: float = field(
        default_factory=lambda: float(os.getenv("CB_STALE_DATA_TIMEOUT", "120.0"))
    )
    fee_rate: float = field(
        default_factory=lambda: float(os.getenv("FEE_RATE", "0.02"))
    )
    stop_loss_pct: float = field(
        default_factory=lambda: float(os.getenv("STOP_LOSS_PCT", "0.0"))
    )
    dashboard_port: int = field(
        default_factory=lambda: int(os.getenv("DASHBOARD_PORT", "8080"))
    )


@dataclass(frozen=True, slots=True)
class Settings:
    poly: PolymarketConfig = field(default_factory=PolymarketConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)


settings = Settings()
