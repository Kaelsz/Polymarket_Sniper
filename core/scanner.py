"""
Market Scanner — Monitors all Polymarket markets for near-resolution opportunities.

Polls the Gamma API for active markets, pre-filters by volume and price,
verifies ask prices on the CLOB order book, and feeds opportunities to the engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp

from core.config import settings
from core.polymarket import polymarket

log = logging.getLogger("polysniper.scanner")

GAMMA_BASE = "https://gamma-api.polymarket.com"
_HEADERS = {"User-Agent": "PolySniper/2.0"}


@dataclass(frozen=True, slots=True)
class Opportunity:
    """A trading opportunity: a token priced near resolution."""

    condition_id: str
    token_id: str
    question: str
    outcome: str
    ask_price: float
    volume: float
    end_date: str = ""
    market_slug: str = ""


class MarketScanner:
    """Scans all Polymarket markets for tokens in the buy-price window."""

    GAME = "Scanner"

    def __init__(
        self,
        queue: asyncio.Queue[Opportunity],
        *,
        circuit_breaker: object | None = None,
        scan_interval: float | None = None,
        min_volume: float | None = None,
    ) -> None:
        self._queue = queue
        self._cb = circuit_breaker
        self._scan_interval = scan_interval or settings.trading.scanner_interval
        self._min_volume = min_volume or settings.trading.min_volume_usdc
        self._seen: set[str] = set()
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._last_scan_markets = 0
        self._last_scan_candidates = 0
        self._total_opportunities = 0

    @property
    def stats(self) -> dict:
        return {
            "markets_scanned": self._last_scan_markets,
            "candidates": self._last_scan_candidates,
            "opportunities_sent": self._total_opportunities,
            "seen_tokens": len(self._seen),
        }

    async def run(self) -> None:
        self._running = True
        self._session = aiohttp.ClientSession(headers=_HEADERS)
        log.info(
            "Scanner started — interval=%ds, volume>=$%.0fK, price=[%.2f–%.2f], end<=%.0fh",
            self._scan_interval,
            self._min_volume / 1000,
            settings.trading.min_buy_price,
            settings.trading.max_buy_price,
            settings.trading.max_end_hours,
        )
        try:
            while self._running:
                try:
                    await self._scan_cycle()
                    self._heartbeat()
                except Exception as exc:
                    log.error("Scan cycle error: %s", exc)
                    self._record_failure(str(exc))
                await asyncio.sleep(self._scan_interval)
        finally:
            if self._session:
                await self._session.close()

    def stop(self) -> None:
        self._running = False

    def _heartbeat(self) -> None:
        if self._cb and hasattr(self._cb, "record_heartbeat"):
            self._cb.record_heartbeat(self.GAME)

    def _record_failure(self, error: str) -> None:
        if self._cb and hasattr(self._cb, "record_failure"):
            self._cb.record_failure(self.GAME, error)

    async def _scan_cycle(self) -> None:
        t0 = time.perf_counter()
        markets = await self._fetch_markets()
        self._last_scan_markets = len(markets)

        candidates = self._pre_filter(markets)
        self._last_scan_candidates = len(candidates)

        new_opps = 0
        for cand in candidates:
            token_id = cand["token_id"]
            if token_id in self._seen:
                continue

            try:
                ask = await polymarket.best_ask(token_id)
            except Exception:
                continue

            if ask is None:
                continue

            if not (settings.trading.min_buy_price <= ask <= settings.trading.max_buy_price):
                continue

            opp = Opportunity(
                condition_id=cand["condition_id"],
                token_id=token_id,
                question=cand["question"],
                outcome=cand["outcome"],
                ask_price=ask,
                volume=cand["volume"],
                end_date=cand.get("end_date", ""),
                market_slug=cand.get("slug", ""),
            )
            self._seen.add(token_id)
            self._total_opportunities += 1
            await self._queue.put(opp)
            new_opps += 1
            log.info(
                "OPPORTUNITY  %s @ $%.4f | %s | vol=$%.0fK",
                opp.outcome, opp.ask_price, opp.question[:80], opp.volume / 1000,
            )

        elapsed = (time.perf_counter() - t0) * 1000
        if new_opps:
            log.info(
                "Scan: %d markets, %d candidates, %d new opportunities (%.0fms)",
                len(markets), len(candidates), new_opps, elapsed,
            )
        else:
            log.debug(
                "Scan: %d markets, %d candidates, 0 new (%.0fms)",
                len(markets), len(candidates), elapsed,
            )

    async def _fetch_markets(self) -> list[dict]:
        """Fetch all active markets from Gamma API with pagination."""
        assert self._session is not None
        all_markets: list[dict] = []
        offset = 0
        limit = 100

        while True:
            url = (
                f"{GAMMA_BASE}/markets"
                f"?active=true&closed=false"
                f"&limit={limit}&offset={offset}"
            )
            try:
                async with self._session.get(url) as resp:
                    if resp.status != 200:
                        log.warning("Gamma API returned %d", resp.status)
                        break
                    data = await resp.json()
            except Exception as exc:
                log.warning("Gamma API fetch error: %s", exc)
                break

            if not data:
                break

            all_markets.extend(data)
            if len(data) < limit:
                break
            offset += limit

        return all_markets

    @staticmethod
    def _parse_end_date(raw: str) -> datetime | None:
        """Parse an ISO end date string from Gamma API."""
        if not raw:
            return None
        try:
            raw = raw.replace("Z", "+00:00")
            return datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            return None

    def _pre_filter(self, markets: list[dict]) -> list[dict]:
        """Pre-filter markets by volume, end date, and indicative price."""
        candidates: list[dict] = []
        min_price = settings.trading.min_buy_price
        max_price = settings.trading.max_buy_price
        now = datetime.now(timezone.utc)
        max_end_seconds = settings.trading.max_end_hours * 3600

        for m in markets:
            volume = float(m.get("volume", 0) or 0)
            if volume < self._min_volume:
                continue

            end_raw = m.get("endDate", "") or m.get("end_date_iso", "") or ""
            end_dt = self._parse_end_date(end_raw)
            if end_dt is None:
                continue
            time_left = (end_dt - now).total_seconds()
            if time_left <= 0 or time_left > max_end_seconds:
                continue

            prices_raw = m.get("outcomePrices", "")
            tokens_raw = m.get("clobTokenIds", "")
            outcomes_raw = m.get("outcomes", "")

            if not prices_raw or not tokens_raw:
                continue

            try:
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                token_ids = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
                outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or ["Yes", "No"])
            except (json.JSONDecodeError, TypeError):
                continue

            condition_id = m.get("conditionId", "")
            question = m.get("question", "")
            slug = m.get("slug", "")
            end_date = m.get("endDate", "") or m.get("end_date_iso", "") or ""

            for price_s, tid, outcome in zip(prices, token_ids, outcomes):
                try:
                    price = float(price_s)
                except (ValueError, TypeError):
                    continue

                if min_price <= price <= max_price:
                    candidates.append({
                        "condition_id": condition_id,
                        "token_id": tid,
                        "question": question,
                        "outcome": outcome,
                        "volume": volume,
                        "price": price,
                        "end_date": end_date,
                        "slug": slug,
                    })

        return candidates

    def clear_seen(self, token_id: str) -> None:
        """Remove a token from the seen set (e.g., after position closed)."""
        self._seen.discard(token_id)
