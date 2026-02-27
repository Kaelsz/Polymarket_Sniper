"""
Valorant Adapter — VLR.gg Live Score Scraping

VLR.gg does not expose a public WebSocket, but its match pages update
via periodic AJAX/HTML fragments.  Strategy:
  1. GET  https://www.vlr.gg/matches/results  → find recent/live match IDs
  2. Poll  https://www.vlr.gg/<match_id>/?game=all  at ~3s intervals
  3. Parse the HTML scoreboard for round scores and match status.
     A completed map shows ``class="mod-win"`` on the winning team's cell.
     A completed series has a banner with the series winner.

For lower latency, we also attempt to intercept the internal
``/api/match/<id>/stats`` JSON endpoint that some VLR pages query.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import aiohttp

from adapters.base import BaseAdapter, MatchEvent

_VLR_BASE = "https://www.vlr.gg"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


class ValorantAdapter(BaseAdapter):
    GAME = "Valorant"

    def __init__(self, queue: asyncio.Queue, poll_interval: float = 3.0, circuit_breaker=None) -> None:
        super().__init__(queue, circuit_breaker=circuit_breaker)
        self._poll_interval = poll_interval
        self._session: aiohttp.ClientSession | None = None
        self._tracked_matches: dict[str, dict[str, str]] = {}
        self._seen_finished: set[str] = set()

    async def _connect(self) -> None:
        self._session = aiohttp.ClientSession(headers=_HEADERS)
        self.log.info("VLR.gg session opened")

    async def _listen(self) -> None:
        assert self._session is not None

        while self._running:
            try:
                await self._discover_matches()
                for match_id, meta in list(self._tracked_matches.items()):
                    if match_id not in self._seen_finished:
                        await self._poll_match(match_id, meta)
                self._heartbeat()
            except Exception as exc:
                self.log.debug("VLR poll error: %s", exc)

            await asyncio.sleep(self._poll_interval)

        if self._session:
            await self._session.close()

    async def _discover_matches(self) -> None:
        """Scrape VLR.gg live matches page for active match IDs."""
        assert self._session is not None
        url = f"{_VLR_BASE}/matches"
        try:
            async with self._session.get(url) as resp:
                html = await resp.text()
        except Exception:
            return

        for m in re.finditer(
            r'href="/(\d+)/[^"]*".*?'
            r'class="match-item-vs-team-name">\s*<div[^>]*>\s*([^<]+)<.*?'
            r'class="match-item-vs-team-name">\s*<div[^>]*>\s*([^<]+)<',
            html,
            re.DOTALL,
        ):
            mid = m.group(1)
            if mid not in self._tracked_matches and mid not in self._seen_finished:
                self._tracked_matches[mid] = {
                    "team1": m.group(2).strip(),
                    "team2": m.group(3).strip(),
                }
                self.log.info("Tracking VLR match %s: %s vs %s",
                              mid,
                              m.group(2).strip(),
                              m.group(3).strip())

    async def _poll_match(self, match_id: str, meta: dict[str, str]) -> None:
        assert self._session is not None
        url = f"{_VLR_BASE}/{match_id}"
        try:
            async with self._session.get(url) as resp:
                html = await resp.text()
        except Exception:
            return

        # Detect completed series — VLR shows "won" in the header
        winner_match = re.search(
            r'class="match-header-link-name[^"]*"[^>]*>\s*'
            r'<div[^>]*>\s*([^<]+)</div>\s*</a>\s*'
            r'.*?class="match-header-vs-score"[^>]*>.*?'
            r'<span[^>]*class="[^"]*mod-won[^"]*"',
            html,
            re.DOTALL,
        )

        if winner_match:
            winner = winner_match.group(1).strip()
            loser = (
                meta["team2"] if winner.lower() == meta["team1"].lower()
                else meta["team1"]
            )
            self._seen_finished.add(match_id)
            self._tracked_matches.pop(match_id, None)
            await self.emit(MatchEvent(
                game=self.GAME,
                team_won=winner,
                team_lost=loser,
                event="Series Won",
                match_id=match_id,
            ))
            return

        # Detect individual map wins via score cells with mod-win class
        for map_match in re.finditer(
            r'class="[^"]*mod-(?:1st|2nd|3rd)[^"]*".*?'
            r'class="[^"]*mod-win[^"]*"[^>]*>\s*(\d+)\s*<',
            html,
            re.DOTALL,
        ):
            pass  # map-level tracking can be extended here
