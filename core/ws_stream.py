"""
WebSocket Price Stream — Real-time market price monitoring.

Connects to Polymarket's CLOB WebSocket for sub-second price updates.
The Gamma scanner still runs (slowly) for market *discovery*, while this
stream handles instant opportunity detection when a price enters the
buy window.

Protocol reference:
  - Endpoint: wss://ws-subscriptions-clob.polymarket.com/ws/market
  - Subscribe: {"assets_ids": [...], "type": "market", "custom_feature_enabled": true}
  - Heartbeat: send "PING" every 10s, expect "PONG"
  - Events: price_change, best_bid_ask, market_resolved
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import websockets

from core.config import settings
from core.scanner import Opportunity

log = logging.getLogger("polysniper.ws")

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL = 9.0
RECONNECT_BASE = 3.0
RECONNECT_MAX = 60.0
EMIT_COOLDOWN = 10.0
SUB_BATCH = 50


@dataclass(frozen=True, slots=True)
class MarketMeta:
    """Metadata for a market eligible for WS monitoring."""

    condition_id: str
    token_id: str
    question: str
    outcome: str
    volume: float
    end_date: str = ""
    market_slug: str = ""


class PriceStream:
    """Real-time WebSocket price stream for Polymarket markets."""

    def __init__(self, queue: asyncio.Queue[Opportunity]) -> None:
        self._queue = queue
        self._markets: dict[str, MarketMeta] = {}
        self._subscribed: set[str] = set()
        self._running = False
        self._connected = False
        self._last_emit: dict[str, float] = {}
        self._reconnect_delay = RECONNECT_BASE
        self._events_received = 0
        self._opportunities_emitted = 0
        self._reconnects = 0
        self._resolution_callbacks: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_markets(self, markets: dict[str, MarketMeta]) -> None:
        """Called by the scanner after each discovery cycle."""
        old = len(self._markets)
        self._markets = dict(markets)
        new = len(self._markets)
        if old != new:
            log.info("WS  Market watchlist: %d → %d tokens", old, new)

    def on_resolution(self, callback) -> None:
        """Register a callback(condition_id: str) for instant resolution."""
        self._resolution_callbacks.append(callback)

    @property
    def stats(self) -> dict:
        return {
            "ws_connected": self._connected,
            "ws_subscribed": len(self._subscribed),
            "ws_monitored": len(self._markets),
            "ws_events": self._events_received,
            "ws_opportunities": self._opportunities_emitted,
            "ws_reconnects": self._reconnects,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        log.info("PriceStream starting — will connect once markets are discovered")
        while self._running:
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._connected = False
                self._reconnects += 1
                log.warning(
                    "WS  disconnected: %s — reconnecting in %.0fs",
                    exc, self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 1.5, RECONNECT_MAX,
                )
        log.info("PriceStream stopped")

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Connection & Streaming
    # ------------------------------------------------------------------

    async def _connect_and_stream(self) -> None:
        token_ids = list(self._markets.keys())
        if not token_ids:
            log.debug("WS  No eligible markets yet — waiting 10s")
            await asyncio.sleep(10)
            return

        async with websockets.connect(
            WS_URL,
            ping_interval=None,
            close_timeout=5,
            max_size=2**20,
        ) as ws:
            self._connected = True
            self._reconnect_delay = RECONNECT_BASE

            await self._subscribe_batch(ws, token_ids)
            self._subscribed = set(token_ids)
            log.info(
                "WS  Connected — subscribed to %d token streams", len(token_ids),
            )

            ping_task = asyncio.create_task(self._ping_loop(ws))
            refresh_task = asyncio.create_task(self._refresh_loop(ws))

            try:
                async for raw_msg in ws:
                    if not isinstance(raw_msg, str):
                        continue
                    if raw_msg == "PONG":
                        continue
                    try:
                        self._handle_raw(raw_msg)
                    except Exception as exc:
                        log.debug("WS  parse error: %s", exc)
            finally:
                ping_task.cancel()
                refresh_task.cancel()
                self._connected = False
                self._subscribed.clear()

    async def _subscribe_batch(self, ws, token_ids: list[str]) -> None:
        for i in range(0, len(token_ids), SUB_BATCH):
            batch = token_ids[i : i + SUB_BATCH]
            payload = json.dumps({
                "assets_ids": batch,
                "type": "market",
                "custom_feature_enabled": True,
            })
            await ws.send(payload)
            if i + SUB_BATCH < len(token_ids):
                await asyncio.sleep(0.05)

    async def _ping_loop(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(PING_INTERVAL)
                await ws.send("PING")
        except (asyncio.CancelledError, Exception):
            pass

    async def _refresh_loop(self, ws) -> None:
        """Subscribe to newly discovered markets without reconnecting."""
        try:
            while True:
                await asyncio.sleep(30)
                current = set(self._markets.keys())
                new_tokens = current - self._subscribed
                if new_tokens:
                    await self._subscribe_batch(ws, list(new_tokens))
                    self._subscribed |= new_tokens
                    log.info(
                        "WS  +%d new subscriptions (total=%d)",
                        len(new_tokens), len(self._subscribed),
                    )
                self._gc_dedup_cache()
        except asyncio.CancelledError:
            pass

    def _gc_dedup_cache(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._last_emit.items() if now - v > EMIT_COOLDOWN * 6]
        for k in expired:
            del self._last_emit[k]

    # ------------------------------------------------------------------
    # Event Handling
    # ------------------------------------------------------------------

    def _handle_raw(self, raw: str) -> None:
        data = json.loads(raw)
        if isinstance(data, list):
            for event in data:
                self._dispatch(event)
        elif isinstance(data, dict):
            self._dispatch(data)

    def _dispatch(self, event: dict) -> None:
        self._events_received += 1
        etype = event.get("event_type") or event.get("type", "")

        if etype == "price_change":
            self._on_price_change(event)
        elif etype == "best_bid_ask":
            self._on_best_bid_ask(event)
        elif etype == "market_resolved":
            self._on_resolution(event)

    def _on_price_change(self, event: dict) -> None:
        for change in event.get("price_changes", []):
            asset_id = change.get("asset_id", "")
            best_ask_s = change.get("best_ask")
            if not best_ask_s or asset_id not in self._markets:
                continue
            try:
                ask = float(best_ask_s)
            except (ValueError, TypeError):
                continue
            self._maybe_emit(asset_id, ask)

    def _on_best_bid_ask(self, event: dict) -> None:
        asset_id = event.get("asset_id", "")
        best_ask_s = event.get("best_ask")
        if not best_ask_s or asset_id not in self._markets:
            return
        try:
            ask = float(best_ask_s)
        except (ValueError, TypeError):
            return
        self._maybe_emit(asset_id, ask)

    def _on_resolution(self, event: dict) -> None:
        condition_id = event.get("market", "")
        if not condition_id:
            return
        log.info("WS  market_resolved event for %s", condition_id[:16])
        for cb in self._resolution_callbacks:
            try:
                cb(condition_id)
            except Exception as exc:
                log.debug("WS  resolution callback error: %s", exc)

    # ------------------------------------------------------------------
    # Opportunity Emission
    # ------------------------------------------------------------------

    def _maybe_emit(self, token_id: str, ask: float) -> None:
        min_p = settings.trading.min_buy_price
        max_p = settings.trading.max_buy_price

        if not (min_p <= ask <= max_p):
            return

        now = time.monotonic()
        if now - self._last_emit.get(token_id, 0) < EMIT_COOLDOWN:
            return

        meta = self._markets.get(token_id)
        if meta is None:
            return

        self._last_emit[token_id] = now

        opp = Opportunity(
            condition_id=meta.condition_id,
            token_id=token_id,
            question=meta.question,
            outcome=meta.outcome,
            ask_price=ask,
            volume=meta.volume,
            end_date=meta.end_date,
            market_slug=meta.market_slug,
        )

        try:
            self._queue.put_nowait(opp)
            self._opportunities_emitted += 1
            log.info(
                "WS-SIGNAL  %s @ $%.4f | %s | vol=$%.0fK",
                meta.outcome, ask, meta.question[:60], meta.volume / 1000,
            )
        except asyncio.QueueFull:
            log.warning("WS  Queue full — dropping opportunity")
