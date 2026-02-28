from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_ENV_PATH)


class ConfigError(Exception):
    """Raised when configuration validation fails."""


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
    api_key: str = field(default_factory=lambda: os.getenv("POLY_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("POLY_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("POLY_API_PASSPHRASE", ""))
    funder: str = field(default_factory=lambda: os.getenv("POLY_FUNDER", ""))
    signature_type: int = field(
        default_factory=lambda: int(os.getenv("POLY_SIGNATURE_TYPE", "0"))
    )

    @property
    def has_api_creds(self) -> bool:
        return bool(self.api_key and self.api_secret and self.api_passphrase)


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True, slots=True)
class TradingConfig:
    min_buy_price: float = field(
        default_factory=lambda: float(os.getenv("MIN_BUY_PRICE", "0.95"))
    )
    max_buy_price: float = field(
        default_factory=lambda: float(os.getenv("MAX_BUY_PRICE", "0.99"))
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
    sizing_mode: str = field(
        default_factory=lambda: os.getenv("SIZING_MODE", "fixed")
    )
    min_order_usdc: float = field(
        default_factory=lambda: float(os.getenv("MIN_ORDER_USDC", "10.0"))
    )
    max_order_usdc: float = field(
        default_factory=lambda: float(os.getenv("MAX_ORDER_USDC", "200.0"))
    )
    kelly_fraction: float = field(
        default_factory=lambda: float(os.getenv("KELLY_FRACTION", "0.25"))
    )
    kelly_win_prob: float = field(
        default_factory=lambda: float(os.getenv("KELLY_WIN_PROB", "0.90"))
    )
    scanner_interval: float = field(
        default_factory=lambda: float(os.getenv("SCANNER_INTERVAL", "30.0"))
    )
    min_volume_usdc: float = field(
        default_factory=lambda: float(os.getenv("MIN_VOLUME_USDC", "100000.0"))
    )
    max_end_hours: float = field(
        default_factory=lambda: float(os.getenv("MAX_END_HOURS", "24.0"))
    )
    api_rate_limit: float = field(
        default_factory=lambda: float(os.getenv("API_RATE_LIMIT", "5.0"))
    )
    api_rate_burst: int = field(
        default_factory=lambda: int(os.getenv("API_RATE_BURST", "10"))
    )


@dataclass(frozen=True, slots=True)
class Settings:
    poly: PolymarketConfig = field(default_factory=PolymarketConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)


_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")


def validate_config(s: Settings) -> list[str]:
    """
    Validate all configuration values at startup.

    Returns a list of error messages. An empty list means config is valid.
    """
    errors: list[str] = []
    p = s.poly
    t = s.trading

    # -- Polymarket credentials --
    if not _HEX_RE.match(p.address):
        errors.append(f"POLYMARKET_ADDRESS must be a hex address (0x...): got '{p.address}'")
    if len(p.address) != 42:
        errors.append(f"POLYMARKET_ADDRESS must be 42 characters (got {len(p.address)})")
    if not _HEX_RE.match(p.private_key):
        errors.append(f"POLY_PRIVATE_KEY must be a hex string (0x...)")
    if not p.host.startswith("http"):
        errors.append(f"POLYMARKET_HOST must be an HTTP(S) URL: got '{p.host}'")

    # -- Trading parameters --
    if not (0.0 <= t.min_buy_price < 1.0):
        errors.append(f"MIN_BUY_PRICE must be in [0, 1.0): got {t.min_buy_price}")
    if not (0.0 < t.max_buy_price <= 1.0):
        errors.append(f"MAX_BUY_PRICE must be in (0, 1.0]: got {t.max_buy_price}")
    if t.min_buy_price >= t.max_buy_price:
        errors.append(
            f"MIN_BUY_PRICE ({t.min_buy_price}) must be less than "
            f"MAX_BUY_PRICE ({t.max_buy_price})"
        )
    if t.order_size_usdc <= 0:
        errors.append(f"ORDER_SIZE_USDC must be > 0: got {t.order_size_usdc}")
    if t.max_open_positions < 1:
        errors.append(f"MAX_OPEN_POSITIONS must be >= 1: got {t.max_open_positions}")
    if t.max_positions_per_game < 1:
        errors.append(f"MAX_POSITIONS_PER_GAME must be >= 1: got {t.max_positions_per_game}")
    if t.max_positions_per_game > t.max_open_positions:
        errors.append(
            f"MAX_POSITIONS_PER_GAME ({t.max_positions_per_game}) "
            f"cannot exceed MAX_OPEN_POSITIONS ({t.max_open_positions})"
        )
    if t.max_session_loss_usdc <= 0:
        errors.append(f"MAX_SESSION_LOSS_USDC must be > 0: got {t.max_session_loss_usdc}")
    if t.max_total_exposure_usdc <= 0:
        errors.append(f"MAX_TOTAL_EXPOSURE_USDC must be > 0: got {t.max_total_exposure_usdc}")
    if t.order_size_usdc > t.max_total_exposure_usdc:
        errors.append(
            f"ORDER_SIZE_USDC ({t.order_size_usdc}) "
            f"cannot exceed MAX_TOTAL_EXPOSURE_USDC ({t.max_total_exposure_usdc})"
        )
    if t.match_cooldown_seconds < 0:
        errors.append(f"MATCH_COOLDOWN_SECONDS must be >= 0: got {t.match_cooldown_seconds}")

    # -- Fees & stop-loss --
    if not (0.0 <= t.fee_rate < 1.0):
        errors.append(f"FEE_RATE must be in [0, 1.0): got {t.fee_rate}")
    if not (0.0 <= t.stop_loss_pct < 1.0):
        errors.append(f"STOP_LOSS_PCT must be in [0, 1.0): got {t.stop_loss_pct}")

    # -- Circuit breaker --
    if t.cb_failure_threshold < 1:
        errors.append(f"CB_FAILURE_THRESHOLD must be >= 1: got {t.cb_failure_threshold}")
    if t.cb_min_healthy_adapters < 0:
        errors.append(f"CB_MIN_HEALTHY_ADAPTERS must be >= 0: got {t.cb_min_healthy_adapters}")
    if t.cb_stale_data_timeout <= 0:
        errors.append(f"CB_STALE_DATA_TIMEOUT must be > 0: got {t.cb_stale_data_timeout}")

    # -- Dashboard --
    if not (1 <= t.dashboard_port <= 65535):
        errors.append(f"DASHBOARD_PORT must be in [1, 65535]: got {t.dashboard_port}")

    # -- Sizing --
    if t.sizing_mode not in ("fixed", "confidence", "kelly"):
        errors.append(f"SIZING_MODE must be 'fixed', 'confidence', or 'kelly': got '{t.sizing_mode}'")
    if t.min_order_usdc <= 0:
        errors.append(f"MIN_ORDER_USDC must be > 0: got {t.min_order_usdc}")
    if t.max_order_usdc <= 0:
        errors.append(f"MAX_ORDER_USDC must be > 0: got {t.max_order_usdc}")
    if t.min_order_usdc > t.max_order_usdc:
        errors.append(
            f"MIN_ORDER_USDC ({t.min_order_usdc}) "
            f"cannot exceed MAX_ORDER_USDC ({t.max_order_usdc})"
        )
    if not (0.0 < t.kelly_fraction <= 1.0):
        errors.append(f"KELLY_FRACTION must be in (0, 1.0]: got {t.kelly_fraction}")
    if not (0.0 < t.kelly_win_prob < 1.0):
        errors.append(f"KELLY_WIN_PROB must be in (0, 1.0): got {t.kelly_win_prob}")

    # -- Scanner --
    if t.scanner_interval <= 0:
        errors.append(f"SCANNER_INTERVAL must be > 0: got {t.scanner_interval}")
    if t.min_volume_usdc < 0:
        errors.append(f"MIN_VOLUME_USDC must be >= 0: got {t.min_volume_usdc}")
    if t.max_end_hours <= 0:
        errors.append(f"MAX_END_HOURS must be > 0: got {t.max_end_hours}")

    # -- Rate limiting --
    if t.api_rate_limit <= 0:
        errors.append(f"API_RATE_LIMIT must be > 0: got {t.api_rate_limit}")
    if t.api_rate_burst < 1:
        errors.append(f"API_RATE_BURST must be >= 1: got {t.api_rate_burst}")

    return errors


settings = Settings()
