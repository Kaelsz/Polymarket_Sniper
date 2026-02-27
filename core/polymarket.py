from __future__ import annotations

import asyncio
import logging
from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType

from core.config import settings
from core.rate_limiter import RateLimiter

log = logging.getLogger("polysniper.polymarket")


class PolymarketClient:
    """Async-friendly wrapper around the py-clob-client SDK."""

    def __init__(self) -> None:
        self._client: ClobClient | None = None
        self._lock = asyncio.Lock()
        self._limiter: RateLimiter | None = None

    def set_rate_limiter(self, limiter: RateLimiter) -> None:
        self._limiter = limiter

    async def _throttle(self) -> None:
        if self._limiter:
            await self._limiter.acquire()

    async def init(self) -> None:
        async with self._lock:
            if self._client is not None:
                return
            cfg = settings.poly
            loop = asyncio.get_running_loop()
            self._client = await loop.run_in_executor(
                None,
                lambda: ClobClient(
                    cfg.host,
                    key=cfg.private_key,
                    chain_id=137,  # Polygon mainnet
                ),
            )
            try:
                api_creds = await loop.run_in_executor(
                    None, self._client.derive_api_key
                )
                await loop.run_in_executor(
                    None, self._client.set_api_creds, api_creds
                )
                log.info("Polymarket CLOB client initialized on Polygon (chain 137)")
            except Exception as exc:
                if settings.trading.dry_run:
                    log.warning(
                        "API key derivation failed (dry-run mode, non-fatal): %s", exc
                    )
                else:
                    raise

    @property
    def client(self) -> ClobClient:
        if self._client is None:
            raise RuntimeError("PolymarketClient not initialized — call await init()")
        return self._client

    async def get_markets(self, **filters: Any) -> list[dict]:
        await self._throttle()
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: self.client.get_markets(**filters)
        )
        return resp

    async def get_order_book(self, token_id: str) -> dict:
        await self._throttle()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.client.get_order_book(token_id)
        )

    async def market_buy(self, token_id: str, amount: float) -> dict | None:
        """Place a market-buy order. Returns None in dry-run mode."""
        if settings.trading.dry_run:
            log.warning("[DRY RUN] Would buy token %s for $%.2f", token_id, amount)
            return None

        await self._throttle()
        loop = asyncio.get_running_loop()
        order_args = OrderArgs(
            token_id=token_id,
            amount=amount,
            price=1.0,  # market buy: willing to pay up to $1
            side="BUY",
        )
        signed = await loop.run_in_executor(
            None,
            lambda: self.client.create_and_post_order(order_args),
        )
        log.info("Order posted: %s", signed)
        return signed

    async def market_sell(self, token_id: str, shares: float) -> dict | None:
        """Place a market-sell order for the given number of shares."""
        if settings.trading.dry_run:
            log.warning("[DRY RUN] Would sell %.2f shares of %s", shares, token_id)
            return None

        await self._throttle()
        loop = asyncio.get_running_loop()
        order_args = OrderArgs(
            token_id=token_id,
            amount=shares,
            price=0.001,
            side="SELL",
        )
        signed = await loop.run_in_executor(
            None,
            lambda: self.client.create_and_post_order(order_args),
        )
        log.info("Sell order posted: %s", signed)
        return signed

    async def best_ask(self, token_id: str) -> float | None:
        """Return the lowest ask price for a YES token, or None if empty."""
        book = await self.get_order_book(token_id)
        asks = book.get("asks", [])
        if not asks:
            return None
        return float(min(asks, key=lambda a: float(a["price"]))["price"])

    async def get_market_resolution(self, condition_id: str) -> str | None:
        """
        Check if a market has officially resolved.

        Returns the winning outcome ("Yes" or "No"), or None if the
        market has not yet resolved.
        """
        try:
            await self._throttle()
            loop = asyncio.get_running_loop()
            market = await loop.run_in_executor(
                None, lambda: self.client.get_market(condition_id)
            )
            if not isinstance(market, dict):
                return None
            if not market.get("resolved"):
                return None
            for token in market.get("tokens", []):
                if token.get("winner"):
                    return token.get("outcome")
            return None
        except Exception as exc:
            log.debug("Resolution check failed for %s: %s", condition_id, exc)
            return None


polymarket = PolymarketClient()
