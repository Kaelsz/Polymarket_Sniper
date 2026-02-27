"""
League of Legends Adapter — lolesports.com Live Stats Feed

Riot Games exposes a public API for live esports:
  1. GET  https://esports-api.lolesports.com/persisted/gw/getLive
     → returns all live matches with ``gameId``, team names, state
  2. GET  https://feed.lolesports.com/livestats/v1/window/<gameId>
     → returns frame-by-frame game state every ~500ms
     The ``frames[-1].gameState`` transitions to ``"finished"``
     when the Nexus is destroyed.  ``blueTeam``/``redTeam`` objects
     contain ``result.outcome = "win" | "loss"``.

  Alternatively, the persisted endpoint ``getEventDetails`` carries
  a ``match.games[].state`` that flips to ``"completed"`` with a
  winner field.

This adapter polls the lightweight ``getLive`` endpoint at ~2s
and upgrades to the ``livestats`` feed for active games to catch
the Nexus destruction frame with sub-second precision.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from adapters.base import BaseAdapter, MatchEvent

_API_BASE = "https://esports-api.lolesports.com/persisted/gw"
_FEED_BASE = "https://feed.lolesports.com/livestats/v1"
_API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"  # public Riot x-api-key

_HEADERS = {
    "x-api-key": _API_KEY,
    "User-Agent": "PolySniper/1.0",
}


class LoLAdapter(BaseAdapter):
    GAME = "LoL"

    def __init__(self, queue: asyncio.Queue, poll_interval: float = 2.0, circuit_breaker=None) -> None:
        super().__init__(queue, circuit_breaker=circuit_breaker)
        self._poll_interval = poll_interval
        self._session: aiohttp.ClientSession | None = None
        self._seen_finished: set[str] = set()

    async def _connect(self) -> None:
        self._session = aiohttp.ClientSession(headers=_HEADERS)
        self.log.info("LoL esports API session opened")

    async def _listen(self) -> None:
        assert self._session is not None

        while self._running:
            try:
                live_games = await self._get_live_games()
                for game in live_games:
                    game_id = game.get("id", "")
                    if game_id in self._seen_finished:
                        continue
                    await self._check_game_state(game_id, game)
                self._heartbeat()
            except Exception as exc:
                self.log.debug("LoL poll error: %s", exc)

            await asyncio.sleep(self._poll_interval)

        if self._session:
            await self._session.close()

    async def _get_live_games(self) -> list[dict[str, Any]]:
        assert self._session is not None
        url = f"{_API_BASE}/getLive"
        async with self._session.get(url, params={"hl": "en-US"}) as resp:
            data = await resp.json()

        games: list[dict[str, Any]] = []
        schedule = data.get("data", {}).get("schedule", {}).get("events", [])
        for event in schedule:
            match = event.get("match", {})
            for g in match.get("games", []):
                g["_teams"] = match.get("teams", [])
                g["_event"] = event.get("league", {}).get("name", "")
                games.append(g)
        return games

    async def _check_game_state(self, game_id: str, game_meta: dict) -> None:
        """Hit the livestats window endpoint to detect Nexus destruction."""
        assert self._session is not None
        url = f"{_FEED_BASE}/window/{game_id}"
        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        except Exception:
            return

        frames = data.get("frames", [])
        if not frames:
            return

        latest = frames[-1]
        state = latest.get("gameState", "")

        if state == "finished":
            self._seen_finished.add(game_id)
            winner, loser = self._extract_winner(latest, game_meta)
            if winner:
                await self.emit(MatchEvent(
                    game=self.GAME,
                    team_won=winner,
                    team_lost=loser,
                    event="Nexus Destroyed",
                    match_id=game_id,
                ))

    @staticmethod
    def _extract_winner(frame: dict, meta: dict) -> tuple[str, str]:
        teams = meta.get("_teams", [])
        team_names = {
            "blue": teams[0].get("name", "Blue") if len(teams) > 0 else "Blue",
            "red": teams[1].get("name", "Red") if len(teams) > 1 else "Red",
        }
        for side in ("blueTeam", "redTeam"):
            side_data = frame.get(side, {})
            result = side_data.get("result", {})
            if result.get("outcome") == "win":
                colour = side.replace("Team", "")
                winner = team_names.get(colour, colour)
                loser_colour = "red" if colour == "blue" else "blue"
                loser = team_names.get(loser_colour, loser_colour)
                return winner, loser
        return "", ""
