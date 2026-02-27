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
            r'class="match-item-vs-team-name">\s*<div[^>]*>\s*(?:<span[^>]*></span>\s*)?([^<]+)<.*?'
            r'class="match-item-vs-team-name">\s*<div[^>]*>\s*(?:<span[^>]*></span>\s*)?([^<]+)<',
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

        if not re.search(r'class="match-header-vs-note[^"]*"[^>]*>\s*final\s*<', html, re.IGNORECASE):
            return

        team1 = self._extract_header_team(html, "mod-1") or meta.get("team1", "")
        team2 = self._extract_header_team(html, "mod-2") or meta.get("team2", "")
        if not team1 or not team2:
            return

        score_block = re.search(
            r'class="match-header-vs-score"[^>]*>.*?'
            r'<span[^>]*class="[^"]*match-header-vs-score-(winner|loser)[^"]*"[^>]*>\s*(\d+)\s*</span>',
            html,
            re.DOTALL,
        )
        if not score_block:
            return

        team1_is_winner = score_block.group(1) == "winner"
        winner = team1 if team1_is_winner else team2
        loser = team2 if team1_is_winner else team1

        self._seen_finished.add(match_id)
        self._tracked_matches.pop(match_id, None)
        await self.emit(MatchEvent(
            game=self.GAME,
            team_won=winner,
            team_lost=loser,
            event="Series Won",
            match_id=match_id,
        ))

    @staticmethod
    def _extract_header_team(html: str, mod_class: str) -> str:
        """Extract team name from match detail header (mod-1 or mod-2)."""
        m = re.search(
            rf'class="match-header-link-name\s+{mod_class}"[^>]*>.*?'
            r'class="wf-title-med[^"]*"[^>]*>\s*([^<]+?)\s*</div>',
            html,
            re.DOTALL,
        )
        return m.group(1).strip() if m else ""
