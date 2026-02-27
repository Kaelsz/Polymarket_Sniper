from __future__ import annotations

import asyncio

import pytest

from adapters.lol_adapter import LoLAdapter


class TestExtractWinner:
    def test_blue_wins(self):
        frame = {
            "blueTeam": {"result": {"outcome": "win"}},
            "redTeam": {"result": {"outcome": "loss"}},
        }
        meta = {"_teams": [{"name": "T1"}, {"name": "GenG"}]}
        winner, loser = LoLAdapter._extract_winner(frame, meta)
        assert winner == "T1"
        assert loser == "GenG"

    def test_red_wins(self):
        frame = {
            "blueTeam": {"result": {"outcome": "loss"}},
            "redTeam": {"result": {"outcome": "win"}},
        }
        meta = {"_teams": [{"name": "DRX"}, {"name": "JDG"}]}
        winner, loser = LoLAdapter._extract_winner(frame, meta)
        assert winner == "JDG"
        assert loser == "DRX"

    def test_no_winner(self):
        frame = {
            "blueTeam": {"result": {}},
            "redTeam": {"result": {}},
        }
        meta = {"_teams": [{"name": "A"}, {"name": "B"}]}
        winner, loser = LoLAdapter._extract_winner(frame, meta)
        assert winner == ""
        assert loser == ""

    def test_missing_teams_fallback(self):
        frame = {
            "blueTeam": {"result": {"outcome": "win"}},
            "redTeam": {"result": {"outcome": "loss"}},
        }
        meta = {"_teams": []}
        winner, loser = LoLAdapter._extract_winner(frame, meta)
        assert winner == "Blue"
        assert loser == "Red"

    def test_single_team_only(self):
        frame = {
            "blueTeam": {"result": {"outcome": "win"}},
            "redTeam": {"result": {"outcome": "loss"}},
        }
        meta = {"_teams": [{"name": "OnlyBlue"}]}
        winner, loser = LoLAdapter._extract_winner(frame, meta)
        assert winner == "OnlyBlue"
        assert loser == "Red"


class TestLoLAdapterCheckGameState:
    @pytest.mark.asyncio
    async def test_finished_game_emits_event(self, event_queue):
        from unittest.mock import AsyncMock, MagicMock
        from aiohttp import ClientSession

        adapter = LoLAdapter(event_queue)
        adapter._running = True

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "frames": [{
                "gameState": "finished",
                "blueTeam": {"result": {"outcome": "win"}},
                "redTeam": {"result": {"outcome": "loss"}},
            }]
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock(spec=ClientSession)
        mock_session.get = MagicMock(return_value=mock_resp)
        adapter._session = mock_session

        game_meta = {"_teams": [{"name": "T1"}, {"name": "GenG"}]}
        await adapter._check_game_state("game_42", game_meta)

        assert not event_queue.empty()
        ev = await event_queue.get()
        assert ev.game == "LoL"
        assert ev.team_won == "T1"
        assert ev.team_lost == "GenG"
        assert ev.event == "Nexus Destroyed"
        assert "game_42" in adapter._seen_finished

    @pytest.mark.asyncio
    async def test_in_progress_no_event(self, event_queue):
        from unittest.mock import AsyncMock, MagicMock
        from aiohttp import ClientSession

        adapter = LoLAdapter(event_queue)
        adapter._running = True

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "frames": [{"gameState": "in_progress"}]
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock(spec=ClientSession)
        mock_session.get = MagicMock(return_value=mock_resp)
        adapter._session = mock_session

        await adapter._check_game_state("game_99", {"_teams": []})
        assert event_queue.empty()

    @pytest.mark.asyncio
    async def test_http_error_no_crash(self, event_queue):
        from unittest.mock import AsyncMock, MagicMock
        from aiohttp import ClientSession

        adapter = LoLAdapter(event_queue)
        adapter._running = True

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock(spec=ClientSession)
        mock_session.get = MagicMock(return_value=mock_resp)
        adapter._session = mock_session

        await adapter._check_game_state("game_err", {"_teams": []})
        assert event_queue.empty()
