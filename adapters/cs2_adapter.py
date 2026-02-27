"""
CS2 Adapter — HLTV Scorebot (Socket.IO)

HLTV exposes a real-time Socket.IO feed ("Scorebot") for every live match.
The connection flow:
  1. GET  https://www.hltv.org/matches  → scrape live match IDs
  2. GET  https://scorebot2.hltv.org/socket.io/?EIO=4&transport=polling
     → obtain a Socket.IO session id (sid)
  3. Upgrade to WebSocket at wss://scorebot2.hltv.org/socket.io/?EIO=4&transport=websocket&sid=<sid>
  4. Emit  ``readyForMatch``  with the match list-id
  5. Listen for ``scoreboard`` and ``log`` events.
     The ``log`` event contains round-by-round actions.  When a match finishes,
     a ``log`` payload with ``"MatchStarted": false`` and a winner field is sent,
     or the scoreboard reaches 16 rounds for one team (MR12→13 in newer format).

This adapter wraps that flow via ``python-socketio[asyncio_client]``.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import aiohttp
import socketio

from adapters.base import BaseAdapter, MatchEvent


_HLTV_MATCHES_URL = "https://www.hltv.org/matches"
_SCOREBOT_URL = "https://scorebot2.hltv.org"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.hltv.org/",
}


class CS2Adapter(BaseAdapter):
    GAME = "CS2"

    def __init__(self, queue: asyncio.Queue, poll_interval: float = 30.0, circuit_breaker=None) -> None:
        super().__init__(queue, circuit_breaker=circuit_breaker)
        self._sio: socketio.AsyncClient | None = None
        self._poll_interval = poll_interval
        self._active_match_ids: set[str] = set()
        self._team_map: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------
    # Step A: Discover live match IDs from HLTV
    # ------------------------------------------------------------------
    async def _fetch_live_match_ids(self) -> list[dict[str, str]]:
        """
        Scrape /matches for live match IDs and team names.
        Returns list of {"id": "...", "team1": "...", "team2": "..."}.
        """
        async with aiohttp.ClientSession(headers=_HEADERS) as sess:
            async with sess.get(_HLTV_MATCHES_URL) as resp:
                html = await resp.text()

        matches: list[dict[str, str]] = []
        # HLTV marks live matches with data-livescore-match
        for m in re.finditer(
            r'href="/matches/(\d+)/([^"]+)".*?'
            r'class="matchTeamName[^"]*">([^<]+)<.*?'
            r'class="matchTeamName[^"]*">([^<]+)<',
            html,
            re.DOTALL,
        ):
            matches.append({
                "id": m.group(1),
                "slug": m.group(2),
                "team1": m.group(3).strip(),
                "team2": m.group(4).strip(),
            })
        return matches

    # ------------------------------------------------------------------
    # Step B: Socket.IO connection to Scorebot
    # ------------------------------------------------------------------
    async def _connect(self) -> None:
        self._sio = socketio.AsyncClient(
            reconnection=False,
            logger=False,
            engineio_logger=False,
        )
        self._register_handlers()
        await self._sio.connect(
            _SCOREBOT_URL,
            transports=["websocket"],
            headers=_HEADERS,
        )

    def _register_handlers(self) -> None:
        sio = self._sio
        assert sio is not None

        @sio.on("scoreboard")
        async def on_scoreboard(data: Any) -> None:
            await self._handle_scoreboard(data)

        @sio.on("log")
        async def on_log(data: Any) -> None:
            await self._handle_log(data)

        @sio.on("disconnect")
        async def on_disconnect() -> None:
            self.log.warning("Scorebot disconnected")

    async def _subscribe_match(self, match_id: str) -> None:
        if self._sio and match_id not in self._active_match_ids:
            await self._sio.emit("readyForMatch", match_id)
            self._active_match_ids.add(match_id)
            self.log.info("Subscribed to match %s", match_id)

    # ------------------------------------------------------------------
    # Step C: Event handlers
    # ------------------------------------------------------------------
    async def _handle_scoreboard(self, data: Any) -> None:
        """Parse scoreboard updates; detect 13-round win (MR12) or 16-round (MR15)."""
        if isinstance(data, str):
            data = json.loads(data)

        ct_score = int(data.get("ctScore", 0))
        t_score = int(data.get("terroristScore", 0))
        ct_name = data.get("ctTeamName", "CT")
        t_name = data.get("terroristTeamName", "T")
        match_id = str(data.get("listId", ""))

        self._team_map[match_id] = {"ct": ct_name, "t": t_name}

        win_threshold = 13  # MR12 (default competitive)
        for score, team, loser in [
            (ct_score, ct_name, t_name),
            (t_score, t_name, ct_name),
        ]:
            if score >= win_threshold:
                await self.emit(MatchEvent(
                    game=self.GAME,
                    team_won=team,
                    team_lost=loser,
                    event="Map Won",
                    match_id=match_id,
                    score=f"{ct_score}-{t_score}",
                ))

    async def _handle_log(self, data: Any) -> None:
        """Parse log events for match-end signals."""
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            return

        if data.get("MatchOver") or data.get("MatchStarted") is False:
            winner = data.get("WinnerName", "")
            loser = data.get("LoserName", "")
            if winner:
                await self.emit(MatchEvent(
                    game=self.GAME,
                    team_won=winner,
                    team_lost=loser,
                    event="Match Ended",
                    match_id=str(data.get("ListId", "")),
                ))

    # ------------------------------------------------------------------
    # Step D: Main listen loop — poll for new matches, keep ws alive
    # ------------------------------------------------------------------
    async def _listen(self) -> None:
        assert self._sio is not None

        while self._running:
            try:
                live = await self._fetch_live_match_ids()
                for m in live:
                    await self._subscribe_match(m["id"])
            except Exception as exc:
                self.log.debug("Match discovery error: %s", exc)

            await asyncio.sleep(self._poll_interval)

        await self._sio.disconnect()
