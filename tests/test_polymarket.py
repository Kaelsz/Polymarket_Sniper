from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestPolymarketClientBestAsk:
    @pytest.mark.asyncio
    async def test_best_ask_returns_lowest(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        book = {
            "asks": [
                {"price": "0.75"},
                {"price": "0.80"},
                {"price": "0.65"},
            ]
        }
        client.get_order_book = AsyncMock(return_value=book)

        result = await client.best_ask("tok_abc")
        assert result == 0.65

    @pytest.mark.asyncio
    async def test_best_ask_empty_book(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        client.get_order_book = AsyncMock(return_value={"asks": []})

        result = await client.best_ask("tok_empty")
        assert result is None

    @pytest.mark.asyncio
    async def test_best_ask_no_asks_key(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        client.get_order_book = AsyncMock(return_value={})

        result = await client.best_ask("tok_missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_best_ask_single_entry(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        client.get_order_book = AsyncMock(return_value={
            "asks": [{"price": "0.42"}]
        })

        result = await client.best_ask("tok_single")
        assert result == 0.42


class TestPolymarketClientMarketBuy:
    @pytest.mark.asyncio
    async def test_dry_run_returns_none(self):
        from core.polymarket import PolymarketClient

        with patch("core.polymarket.settings") as mock_settings:
            mock_settings.trading.dry_run = True
            client = PolymarketClient()
            result = await client.market_buy("tok_test", 50.0)
            assert result is None


class TestPolymarketClientMarketSell:
    @pytest.mark.asyncio
    async def test_dry_run_returns_none(self):
        from core.polymarket import PolymarketClient

        with patch("core.polymarket.settings") as mock_settings:
            mock_settings.trading.dry_run = True
            client = PolymarketClient()
            result = await client.market_sell("tok_test", 100.0)
            assert result is None


class TestPolymarketClientMarketResolution:
    @pytest.mark.asyncio
    async def test_resolved_yes_winner(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        client._client = MagicMock()
        client._client.get_market.return_value = {
            "resolved": True,
            "tokens": [
                {"token_id": "tok_yes", "outcome": "Yes", "winner": True},
                {"token_id": "tok_no", "outcome": "No", "winner": False},
            ],
        }

        result = await client.get_market_resolution("cond_123")
        assert result == "Yes"

    @pytest.mark.asyncio
    async def test_resolved_no_winner(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        client._client = MagicMock()
        client._client.get_market.return_value = {
            "resolved": True,
            "tokens": [
                {"token_id": "tok_yes", "outcome": "Yes", "winner": False},
                {"token_id": "tok_no", "outcome": "No", "winner": True},
            ],
        }

        result = await client.get_market_resolution("cond_123")
        assert result == "No"

    @pytest.mark.asyncio
    async def test_unresolved_returns_none(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        client._client = MagicMock()
        client._client.get_market.return_value = {
            "resolved": False,
            "tokens": [
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
        }

        result = await client.get_market_resolution("cond_123")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_exception_returns_none(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        client._client = MagicMock()
        client._client.get_market.side_effect = Exception("API error")

        result = await client.get_market_resolution("cond_123")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_dict_response_returns_none(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        client._client = MagicMock()
        client._client.get_market.return_value = "not a dict"

        result = await client.get_market_resolution("cond_123")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolved_but_no_winner_returns_none(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        client._client = MagicMock()
        client._client.get_market.return_value = {
            "resolved": True,
            "tokens": [
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
        }

        result = await client.get_market_resolution("cond_123")
        assert result is None


class TestPolymarketClientInit:
    def test_client_not_initialized_raises(self):
        from core.polymarket import PolymarketClient

        client = PolymarketClient()
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = client.client
