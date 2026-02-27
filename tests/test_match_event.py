from __future__ import annotations

import time

from adapters.base import MatchEvent


class TestMatchEvent:
    def test_required_fields(self):
        ev = MatchEvent(game="CS2", team_won="NAVI", event="Match Ended")
        assert ev.game == "CS2"
        assert ev.team_won == "NAVI"
        assert ev.event == "Match Ended"

    def test_default_fields(self):
        ev = MatchEvent(game="LoL", team_won="T1", event="Nexus Destroyed")
        assert ev.match_id == ""
        assert ev.team_lost == ""
        assert ev.score == ""
        assert isinstance(ev.timestamp, float)
        assert ev.timestamp <= time.time()

    def test_all_fields(self):
        ts = 1700000000.0
        ev = MatchEvent(
            game="Dota2",
            team_won="Spirit",
            event="Ancient Destroyed",
            match_id="12345",
            team_lost="Liquid",
            score="2-1",
            timestamp=ts,
        )
        assert ev.match_id == "12345"
        assert ev.team_lost == "Liquid"
        assert ev.score == "2-1"
        assert ev.timestamp == ts

    def test_to_dict(self):
        ev = MatchEvent(game="Valorant", team_won="SEN", event="Series Won")
        d = ev.to_dict()
        assert isinstance(d, dict)
        assert d["game"] == "Valorant"
        assert d["team_won"] == "SEN"
        assert d["event"] == "Series Won"
        assert "timestamp" in d

    def test_frozen(self):
        ev = MatchEvent(game="CS2", team_won="NAVI", event="Map Won")
        try:
            ev.game = "LoL"  # type: ignore[misc]
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass
