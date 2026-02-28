from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("POLYMARKET_ADDRESS", "0xTestAddress")
os.environ.setdefault("POLY_PRIVATE_KEY", "0xdeadbeef")
os.environ.setdefault("POLYMARKET_HOST", "https://clob.polymarket.com")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("MIN_BUY_PRICE", "0.0")
os.environ.setdefault("MAX_BUY_PRICE", "0.85")
os.environ.setdefault("ORDER_SIZE_USDC", "50.0")
os.environ.setdefault("SCANNER_INTERVAL", "30")
os.environ.setdefault("MIN_VOLUME_USDC", "100000")
os.environ.setdefault("MAX_END_HOURS", "2")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_CHAT_ID", "")


@pytest.fixture
def event_queue():
    return asyncio.Queue()
