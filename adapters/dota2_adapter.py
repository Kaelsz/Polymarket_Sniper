"""
Dota 2 Adapter — TrackDota / Steam Game Coordinator

Two viable data sources:
  1. TrackDota API (public, no auth):
     GET https://www.trackdota.com/api/live  → list of live pro matches
     GET https://www.trackdota.com/api/match/<id>  → detailed state
     The ``winner`` field appears when Ancient falls.

  2. Steam WebAPI (requires free API key):
     GET https://api.steampowered.com/IDOTA2Match_570/GetLiveLeagueGames/v1/
     Returns live league games with ``radiant_score``, ``dire_score``,
     ``scoreboard.duration``.  When the game ends it disappears from the feed,
     so we detect "game finished" by absence + last-known state.

This adapter uses TrackDota as the primary source.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from adapters.base import BaseAdapter, MatchEvent

_TRACKDOTA_LIVE = "https://www.trackdota.com/api/live"
_TRACKDOTA_MATCH = "https://www.trackdota.com/api/match/{match_id}"

_HEADERS = {
    "User-Agent": "PolySniper/1.0",
    "Accept": "application/json",
}


class Dota2Adapter(BaseAdapter):
    GAME = "Dota2"

    def __init__(self, queue: asyncio.Queue, poll_interval: float = 3.0, circuit_breaker=None) -> None:
        super().__init__(queue, circuit_breaker=circuit_breaker)
        self._poll_interval = poll_interval
        self._session: aiohttp.ClientSession | None = None
        self._tracked: dict[str, dict[str, Any]] = {}
        self._seen_finished: set[str] = set()

    async def _connect(self) -> None:
        self._session = aiohttp.ClientSession(headers=_HEADERS)
        self.log.info("TrackDota session opened")

    async def _listen(self) -> None:
        assert self._session is not None

        while self._running:
            try:
                await self._poll_live()
                self._heartbeat()
            except Exception as exc:
                self.log.debug("Dota2 poll error: %s", exc)

            await asyncio.sleep(self._poll_interval)

        if self._session:
            await self._session.close()

    async def _poll_live(self) -> None:
        assert self._session is not None

        try:
            async with self._session.get(_TRACKDOTA_LIVE) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        except Exception:
            return

        live_matches = data if isinstance(data, list) else data.get("matches", [])
        current_ids: set[str] = set()

        for match in live_matches:
            mid = str(match.get("match_id", match.get("id", "")))
            if not mid:
                continue
            current_ids.add(mid)

            radiant = match.get("radiant_team", {})
            dire = match.get("dire_team", {})
            self._tracked[mid] = {
                "radiant_name": radiant.get("team_name", "Radiant"),
                "dire_name": dire.get("team_name", "Dire"),
                "radiant_score": match.get("radiant_score", 0),
                "dire_score": match.get("dire_score", 0),
            }

            # Check if the match JSON already has a winner
            winner = match.get("winner")
            if winner and mid not in self._seen_finished:
                await self._emit_winner(mid, winner)

        # Detect games that vanished from the live feed (= just finished)
        disappeared = set(self._tracked.keys()) - current_ids - self._seen_finished
        for mid in disappeared:
            await self._check_finished(mid)

    async def _check_finished(self, match_id: str) -> None:
        """Query the individual match endpoint to confirm result."""
        assert self._session is not None
        url = _TRACKDOTA_MATCH.format(match_id=match_id)
        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        except Exception:
            return

        winner = data.get("winner")
        if winner:
            await self._emit_winner(match_id, winner)

    async def _emit_winner(self, match_id: str, winner: Any) -> None:
        self._seen_finished.add(match_id)
        meta = self._tracked.get(match_id, {})

        # winner can be 1 (Radiant) or 2 (Dire), or a string
        if winner in (1, "1", "radiant"):
            team_won = meta.get("radiant_name", "Radiant")
            team_lost = meta.get("dire_name", "Dire")
        elif winner in (2, "2", "dire"):
            team_won = meta.get("dire_name", "Dire")
            team_lost = meta.get("radiant_name", "Radiant")
        else:
            team_won = str(winner)
            team_lost = ""

        await self.emit(MatchEvent(
            game=self.GAME,
            team_won=team_won,
            team_lost=team_lost,
            event="Ancient Destroyed",
            match_id=match_id,
        ))

        self._tracked.pop(match_id, None)
