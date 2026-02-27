from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("POLYMARKET_ADDRESS", "0xTestAddress")
os.environ.setdefault("POLY_PRIVATE_KEY", "0xdeadbeef")
os.environ.setdefault("POLYMARKET_HOST", "https://clob.polymarket.com")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("MAX_BUY_PRICE", "0.85")
os.environ.setdefault("ORDER_SIZE_USDC", "50.0")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_CHAT_ID", "")


@pytest.fixture
def event_queue():
    return asyncio.Queue()


@pytest.fixture
def sample_markets():
    """Fake Polymarket markets resembling real esport questions."""
    return [
        {
            "condition_id": "cond_cs2_navi",
            "question": "Will Natus Vincere win CS2 Map 1?",
            "tokens": [
                {"token_id": "tok_yes_navi", "outcome": "Yes"},
                {"token_id": "tok_no_navi", "outcome": "No"},
            ],
        },
        {
            "condition_id": "cond_lol_t1",
            "question": "Will T1 win League of Legends Worlds Finals?",
            "tokens": [
                {"token_id": "tok_yes_t1", "outcome": "Yes"},
                {"token_id": "tok_no_t1", "outcome": "No"},
            ],
        },
        {
            "condition_id": "cond_val_sen",
            "question": "Will Sentinels win Valorant VCT Americas?",
            "tokens": [
                {"token_id": "tok_yes_sen", "outcome": "Yes"},
                {"token_id": "tok_no_sen", "outcome": "No"},
            ],
        },
        {
            "condition_id": "cond_dota_spirit",
            "question": "Will Team Spirit win Dota 2 The International?",
            "tokens": [
                {"token_id": "tok_yes_spirit", "outcome": "Yes"},
                {"token_id": "tok_no_spirit", "outcome": "No"},
            ],
        },
        {
            "condition_id": "cond_politics",
            "question": "Will candidate X win the election?",
            "tokens": [
                {"token_id": "tok_yes_pol", "outcome": "Yes"},
                {"token_id": "tok_no_pol", "outcome": "No"},
            ],
        },
    ]


@pytest.fixture
def mock_polymarket_get_markets(sample_markets):
    """Patch Gamma API to return empty (so we use CLOB fallback), and polymarket.get_markets for sample data."""
    with (
        patch("core.mapper.fetch_all_esport_markets", new_callable=AsyncMock, return_value=[]),
        patch("core.mapper.polymarket") as mock_pm,
    ):
        mock_pm.get_markets = AsyncMock(return_value=sample_markets)
        yield mock_pm
