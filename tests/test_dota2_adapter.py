from __future__ import annotations

import asyncio

import pytest

from adapters.dota2_adapter import Dota2Adapter
from adapters.base import MatchEvent


class TestDota2EmitWinner:
    @pytest.mark.asyncio
    async def test_radiant_wins_int(self, event_queue):
        adapter = Dota2Adapter(event_queue)
        adapter._tracked["100"] = {
            "radiant_name": "Team Spirit",
            "dire_name": "Team Liquid",
        }
        await adapter._emit_winner("100", 1)
        ev = await event_queue.get()
        assert ev.team_won == "Team Spirit"
        assert ev.team_lost == "Team Liquid"
        assert ev.event == "Ancient Destroyed"
        assert "100" in adapter._seen_finished
        assert "100" not in adapter._tracked

    @pytest.mark.asyncio
    async def test_dire_wins_int(self, event_queue):
        adapter = Dota2Adapter(event_queue)
        adapter._tracked["200"] = {
            "radiant_name": "OG",
            "dire_name": "Tundra",
        }
        await adapter._emit_winner("200", 2)
        ev = await event_queue.get()
        assert ev.team_won == "Tundra"
        assert ev.team_lost == "OG"

    @pytest.mark.asyncio
    async def test_radiant_wins_string(self, event_queue):
        adapter = Dota2Adapter(event_queue)
        adapter._tracked["300"] = {
            "radiant_name": "PSG.LGD",
            "dire_name": "Xtreme",
        }
        await adapter._emit_winner("300", "1")
        ev = await event_queue.get()
        assert ev.team_won == "PSG.LGD"

    @pytest.mark.asyncio
    async def test_dire_wins_string(self, event_queue):
        adapter = Dota2Adapter(event_queue)
        adapter._tracked["400"] = {
            "radiant_name": "Alliance",
            "dire_name": "Nigma",
        }
        await adapter._emit_winner("400", "dire")
        ev = await event_queue.get()
        assert ev.team_won == "Nigma"

    @pytest.mark.asyncio
    async def test_unknown_winner_format(self, event_queue):
        adapter = Dota2Adapter(event_queue)
        adapter._tracked["500"] = {
            "radiant_name": "A",
            "dire_name": "B",
        }
        await adapter._emit_winner("500", "SomeTeamName")
        ev = await event_queue.get()
        assert ev.team_won == "SomeTeamName"
        assert ev.team_lost == ""

    @pytest.mark.asyncio
    async def test_no_tracked_meta_fallback(self, event_queue):
        adapter = Dota2Adapter(event_queue)
        await adapter._emit_winner("999", 1)
        ev = await event_queue.get()
        assert ev.team_won == "Radiant"
        assert ev.team_lost == "Dire"


class TestDota2PollLive:
    @pytest.mark.asyncio
    async def test_winner_in_payload(self, event_queue):
        from unittest.mock import AsyncMock, MagicMock

        adapter = Dota2Adapter(event_queue)

        live_data = [
            {
                "match_id": "7001",
                "radiant_team": {"team_name": "Spirit"},
                "dire_team": {"team_name": "Liquid"},
                "radiant_score": 30,
                "dire_score": 20,
                "winner": 1,
            }
        ]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=live_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        adapter._session = mock_session

        await adapter._poll_live()

        ev = await event_queue.get()
        assert ev.team_won == "Spirit"
        assert ev.game == "Dota2"

    @pytest.mark.asyncio
    async def test_disappeared_match_triggers_check(self, event_queue):
        from unittest.mock import AsyncMock, MagicMock, patch

        adapter = Dota2Adapter(event_queue)
        adapter._tracked["old_match"] = {
            "radiant_name": "OG",
            "dire_name": "Tundra",
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=[])
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        adapter._session = mock_session

        with patch.object(adapter, "_check_finished", new_callable=AsyncMock) as mock_check:
            await adapter._poll_live()
            mock_check.assert_called_once_with("old_match")
