"""Tests for the backtesting framework."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest.runner import BacktestRunner
from backtest.report import BacktestReport
from backtest.scenario import Scenario, ScenarioEvent, load_scenario


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _event(
    game="CS2", team="NAVI", ask=0.60, resolution="win",
    match_id="m1", token_id="tok1", ts=0.0,
) -> ScenarioEvent:
    return ScenarioEvent(
        timestamp=ts, game=game, team_won=team, ask_price=ask,
        resolution=resolution, match_id=match_id, token_id=token_id,
    )


def _scenario(events: list[ScenarioEvent], **kwargs) -> Scenario:
    defaults = dict(name="Test", events=events)
    defaults.update(kwargs)
    return Scenario(**defaults)


# ------------------------------------------------------------------
# Scenario loading
# ------------------------------------------------------------------
class TestScenarioLoading:
    def test_load_from_json(self, tmp_path):
        data = {
            "name": "My Test",
            "events": [
                {"game": "CS2", "team_won": "NAVI", "ask_price": 0.55, "resolution": "win"},
                {"game": "LoL", "team_won": "T1", "ask_price": 0.70, "resolution": "loss"},
            ],
        }
        path = tmp_path / "test.json"
        path.write_text(json.dumps(data))
        sc = load_scenario(path)
        assert sc.name == "My Test"
        assert len(sc.events) == 2
        assert sc.events[0].game == "CS2"
        assert sc.events[1].resolution == "loss"

    def test_load_with_config_overrides(self, tmp_path):
        data = {
            "name": "Custom",
            "max_buy_price": 0.75,
            "order_size_usdc": 100.0,
            "fee_rate": 0.03,
            "events": [
                {"game": "CS2", "team_won": "NAVI", "ask_price": 0.50, "resolution": "win"},
            ],
        }
        path = tmp_path / "custom.json"
        path.write_text(json.dumps(data))
        sc = load_scenario(path)
        assert sc.max_buy_price == 0.75
        assert sc.order_size_usdc == 100.0
        assert sc.fee_rate == 0.03

    def test_load_skips_malformed_events(self, tmp_path):
        data = {
            "events": [
                {"game": "CS2", "team_won": "NAVI"},
                {"bad": "event"},
                {"game": "LoL", "team_won": "T1"},
            ],
        }
        path = tmp_path / "partial.json"
        path.write_text(json.dumps(data))
        sc = load_scenario(path)
        assert len(sc.events) == 2

    def test_load_defaults_for_missing_fields(self, tmp_path):
        data = {"events": [{"game": "CS2", "team_won": "NAVI"}]}
        path = tmp_path / "minimal.json"
        path.write_text(json.dumps(data))
        sc = load_scenario(path)
        ev = sc.events[0]
        assert ev.ask_price == 0.50
        assert ev.event == "Match Ended"
        assert ev.resolution == ""

    def test_duration(self):
        sc = _scenario([
            _event(ts=100.0),
            _event(ts=500.0, match_id="m2", token_id="t2"),
        ])
        assert sc.duration_s == 400.0

    def test_duration_single_event(self):
        sc = _scenario([_event(ts=100.0)])
        assert sc.duration_s == 0.0


# ------------------------------------------------------------------
# Runner — basic trades
# ------------------------------------------------------------------
class TestRunnerBasic:
    def test_single_win(self):
        sc = _scenario([_event(ask=0.60, resolution="win")])
        report = BacktestRunner(sc).run()
        assert report.wins == 1
        assert report.losses == 0
        assert report.total_pnl > 0

    def test_single_loss(self):
        sc = _scenario([_event(ask=0.60, resolution="loss")])
        report = BacktestRunner(sc).run()
        assert report.wins == 0
        assert report.losses == 1
        assert report.total_pnl < 0

    def test_multiple_trades_pnl(self):
        sc = _scenario([
            _event(ask=0.50, resolution="win", match_id="m1", token_id="t1"),
            _event(ask=0.60, resolution="loss", match_id="m2", token_id="t2", game="LoL"),
        ])
        report = BacktestRunner(sc).run()
        assert report.executed_trades == 2
        assert report.wins == 1
        assert report.losses == 1

    def test_unresolved_position(self):
        sc = _scenario([_event(ask=0.60, resolution="")])
        report = BacktestRunner(sc).run()
        assert report.unresolved == 1
        assert report.open_positions == 1

    def test_empty_scenario(self):
        sc = _scenario([])
        report = BacktestRunner(sc).run()
        assert report.total_events == 0
        assert report.total_pnl == 0.0


# ------------------------------------------------------------------
# Runner — price veto
# ------------------------------------------------------------------
class TestRunnerPriceVeto:
    def test_price_above_max_vetoed(self):
        sc = _scenario(
            [_event(ask=0.90, resolution="win")],
            max_buy_price=0.85,
        )
        report = BacktestRunner(sc).run()
        assert report.vetoed_trades == 1
        assert report.executed_trades == 0

    def test_price_at_max_allowed(self):
        sc = _scenario(
            [_event(ask=0.85, resolution="win")],
            max_buy_price=0.85,
        )
        report = BacktestRunner(sc).run()
        assert report.executed_trades == 1


# ------------------------------------------------------------------
# Runner — risk vetoes
# ------------------------------------------------------------------
class TestRunnerRiskVeto:
    def test_dedup_blocks_same_trade(self):
        sc = _scenario([
            _event(ask=0.50, resolution="win", match_id="m1", token_id="t1"),
            _event(ask=0.55, resolution="win", match_id="m1", token_id="t1"),
        ])
        report = BacktestRunner(sc).run()
        assert report.executed_trades == 1
        assert report.vetoed_trades == 1


# ------------------------------------------------------------------
# Runner — fees
# ------------------------------------------------------------------
class TestRunnerFees:
    def test_fees_deducted_on_win(self):
        sc = _scenario(
            [_event(ask=0.50, resolution="win")],
            fee_rate=0.10,
        )
        report = BacktestRunner(sc).run()
        assert report.total_fees > 0
        assert report.total_pnl < report.gross_pnl

    def test_no_fees_on_loss(self):
        sc = _scenario(
            [_event(ask=0.50, resolution="loss")],
            fee_rate=0.10,
        )
        report = BacktestRunner(sc).run()
        assert report.total_fees == 0

    def test_zero_fee_rate(self):
        sc = _scenario(
            [_event(ask=0.50, resolution="win")],
            fee_rate=0.0,
        )
        report = BacktestRunner(sc).run()
        assert report.total_fees == 0
        assert report.total_pnl == report.gross_pnl


# ------------------------------------------------------------------
# Report
# ------------------------------------------------------------------
class TestReport:
    def test_win_rate_calculation(self):
        sc = _scenario([
            _event(ask=0.50, resolution="win", match_id="m1", token_id="t1"),
            _event(ask=0.60, resolution="win", match_id="m2", token_id="t2", game="LoL"),
            _event(ask=0.70, resolution="loss", match_id="m3", token_id="t3", game="Valorant"),
        ])
        report = BacktestRunner(sc).run()
        assert abs(report.win_rate - 66.7) < 0.1

    def test_roi_calculation(self):
        sc = _scenario([_event(ask=0.50, resolution="win")], order_size_usdc=100.0, fee_rate=0.0)
        report = BacktestRunner(sc).run()
        assert report.roi_pct > 0
        assert report.total_invested == 100.0

    def test_per_game_breakdown(self):
        sc = _scenario([
            _event(ask=0.50, resolution="win", game="CS2", match_id="m1", token_id="t1"),
            _event(ask=0.60, resolution="loss", game="LoL", match_id="m2", token_id="t2"),
        ])
        report = BacktestRunner(sc).run()
        assert "CS2" in report.per_game
        assert "LoL" in report.per_game
        assert report.per_game["CS2"]["wins"] == 1
        assert report.per_game["LoL"]["losses"] == 1

    def test_max_drawdown(self):
        sc = _scenario([
            _event(ask=0.50, resolution="win", match_id="m1", token_id="t1"),
            _event(ask=0.60, resolution="loss", match_id="m2", token_id="t2", game="LoL"),
            _event(ask=0.70, resolution="loss", match_id="m3", token_id="t3", game="Valorant"),
        ])
        report = BacktestRunner(sc).run()
        assert report.max_drawdown > 0

    def test_summary_string(self):
        sc = _scenario([_event(ask=0.50, resolution="win")])
        report = BacktestRunner(sc).run()
        text = report.summary()
        assert "BACKTEST REPORT" in text
        assert "Win Rate" in text
        assert "Net PnL" in text
        assert "Max Drawdown" in text

    def test_veto_reasons_tracked(self):
        sc = _scenario(
            [_event(ask=0.90, resolution="win")],
            max_buy_price=0.85,
        )
        report = BacktestRunner(sc).run()
        assert len(report.veto_reasons) == 1


# ------------------------------------------------------------------
# Example scenario file
# ------------------------------------------------------------------
class TestExampleScenario:
    def test_example_scenario_loads_and_runs(self):
        path = Path("scenarios/example_session.json")
        if not path.exists():
            pytest.skip("Example scenario file not found")
        sc = load_scenario(path)
        report = BacktestRunner(sc).run()
        assert report.total_events == 12
        assert report.executed_trades == 11
        assert report.vetoed_trades == 1
        assert report.wins + report.losses == 11
        assert report.total_pnl != 0
