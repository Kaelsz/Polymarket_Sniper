from __future__ import annotations

import asyncio
import json

import pytest

from adapters.cs2_adapter import CS2Adapter
from adapters.base import MatchEvent


class TestCS2Scoreboard:
    @pytest.mark.asyncio
    async def test_map_won_ct_reaches_13(self, event_queue):
        adapter = CS2Adapter(event_queue)
        data = {
            "ctScore": 13,
            "terroristScore": 9,
            "ctTeamName": "NAVI",
            "terroristTeamName": "G2",
            "listId": "9001",
        }
        await adapter._handle_scoreboard(data)
        assert not event_queue.empty()
        ev: MatchEvent = await event_queue.get()
        assert ev.game == "CS2"
        assert ev.team_won == "NAVI"
        assert ev.team_lost == "G2"
        assert ev.event == "Map Won"
        assert ev.score == "13-9"

    @pytest.mark.asyncio
    async def test_map_won_t_reaches_13(self, event_queue):
        adapter = CS2Adapter(event_queue)
        data = {
            "ctScore": 7,
            "terroristScore": 13,
            "ctTeamName": "Astralis",
            "terroristTeamName": "Vitality",
            "listId": "9002",
        }
        await adapter._handle_scoreboard(data)
        ev: MatchEvent = await event_queue.get()
        assert ev.team_won == "Vitality"
        assert ev.team_lost == "Astralis"

    @pytest.mark.asyncio
    async def test_no_event_below_threshold(self, event_queue):
        adapter = CS2Adapter(event_queue)
        data = {
            "ctScore": 10,
            "terroristScore": 8,
            "ctTeamName": "FaZe",
            "terroristTeamName": "Liquid",
            "listId": "9003",
        }
        await adapter._handle_scoreboard(data)
        assert event_queue.empty()

    @pytest.mark.asyncio
    async def test_scoreboard_string_json(self, event_queue):
        adapter = CS2Adapter(event_queue)
        data = json.dumps({
            "ctScore": 13,
            "terroristScore": 11,
            "ctTeamName": "Cloud9",
            "terroristTeamName": "Spirit",
            "listId": "9004",
        })
        await adapter._handle_scoreboard(data)
        ev = await event_queue.get()
        assert ev.team_won == "Cloud9"

    @pytest.mark.asyncio
    async def test_team_map_updated(self, event_queue):
        adapter = CS2Adapter(event_queue)
        data = {
            "ctScore": 5,
            "terroristScore": 3,
            "ctTeamName": "MOUZ",
            "terroristTeamName": "BIG",
            "listId": "9005",
        }
        await adapter._handle_scoreboard(data)
        assert adapter._team_map["9005"] == {"ct": "MOUZ", "t": "BIG"}


class TestCS2Log:
    @pytest.mark.asyncio
    async def test_match_over_emits(self, event_queue):
        adapter = CS2Adapter(event_queue)
        data = {
            "MatchOver": True,
            "WinnerName": "NAVI",
            "LoserName": "G2",
            "ListId": "8001",
        }
        await adapter._handle_log(data)
        ev = await event_queue.get()
        assert ev.event == "Match Ended"
        assert ev.team_won == "NAVI"
        assert ev.match_id == "8001"

    @pytest.mark.asyncio
    async def test_match_started_false_emits(self, event_queue):
        adapter = CS2Adapter(event_queue)
        data = {
            "MatchStarted": False,
            "WinnerName": "Vitality",
            "LoserName": "Astralis",
            "ListId": "8002",
        }
        await adapter._handle_log(data)
        ev = await event_queue.get()
        assert ev.team_won == "Vitality"

    @pytest.mark.asyncio
    async def test_no_winner_no_event(self, event_queue):
        adapter = CS2Adapter(event_queue)
        data = {"MatchOver": True, "WinnerName": "", "ListId": "8003"}
        await adapter._handle_log(data)
        assert event_queue.empty()

    @pytest.mark.asyncio
    async def test_non_dict_ignored(self, event_queue):
        adapter = CS2Adapter(event_queue)
        await adapter._handle_log([1, 2, 3])
        assert event_queue.empty()

    @pytest.mark.asyncio
    async def test_log_string_json(self, event_queue):
        adapter = CS2Adapter(event_queue)
        data = json.dumps({
            "MatchOver": True,
            "WinnerName": "FaZe",
            "LoserName": "Liquid",
            "ListId": "8004",
        })
        await adapter._handle_log(data)
        ev = await event_queue.get()
        assert ev.team_won == "FaZe"

    @pytest.mark.asyncio
    async def test_irrelevant_log_ignored(self, event_queue):
        adapter = CS2Adapter(event_queue)
        data = {"RoundEnd": True, "Winner": "CT"}
        await adapter._handle_log(data)
        assert event_queue.empty()
