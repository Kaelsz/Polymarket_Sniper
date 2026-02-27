"""Tests for the StateStore persistence layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.persistence import StateStore
from core.risk import RiskConfig, RiskManager


def _cfg(**overrides) -> RiskConfig:
    defaults = dict(
        max_open_positions=10,
        max_positions_per_game=4,
        max_session_loss_usdc=200.0,
        max_total_exposure_usdc=500.0,
        match_cooldown_seconds=0.0,
        dedup_window_seconds=300.0,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)


class TestStateStoreRoundTrip:
    def test_save_and_load_empty(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        rm = RiskManager(_cfg())
        store.save(rm)

        rm2 = RiskManager(_cfg())
        assert store.load(rm2)
        assert rm2.open_positions == 0
        assert rm2.session_pnl == 0.0

    def test_save_and_load_with_positions(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60, condition_id="cond1")
        rm.record_trade("tok2", "LoL", "T1", "m2", 30.0, 0.45, condition_id="cond2")
        rm.record_pnl(15.0)

        store.save(rm)

        rm2 = RiskManager(_cfg())
        assert store.load(rm2)
        assert rm2.open_positions == 2
        assert rm2.session_pnl == pytest.approx(15.0)
        assert rm2.total_exposure == pytest.approx(80.0)
        assert rm2._positions[0].token_id == "tok1"
        assert rm2._positions[0].condition_id == "cond1"
        assert rm2._positions[1].token_id == "tok2"

    def test_save_and_load_with_halt(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        rm = RiskManager(_cfg())
        rm.halt("Test halt")
        store.save(rm)

        rm2 = RiskManager(_cfg())
        store.load(rm2)
        assert rm2.halted
        assert rm2.halt_reason == "Test halt"

    def test_save_and_load_preserves_dedup_keys(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        store.save(rm)

        rm2 = RiskManager(_cfg())
        store.load(rm2)
        decision = rm2.pre_trade_check("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        assert not decision
        assert "Duplicate" in decision.reason

    def test_save_and_load_preserves_cooldowns(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        rm = RiskManager(_cfg(match_cooldown_seconds=9999.0))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        store.save(rm)

        rm2 = RiskManager(_cfg(match_cooldown_seconds=9999.0))
        store.load(rm2)
        decision = rm2.pre_trade_check("tok2", "CS2", "G2", "m1", 50.0, 0.55)
        assert not decision
        assert "cooldown" in decision.reason.lower()


class TestStateStoreEdgeCases:
    def test_load_missing_file_returns_false(self, tmp_path):
        store = StateStore(tmp_path / "nonexistent.json")
        rm = RiskManager(_cfg())
        assert not store.load(rm)
        assert rm.open_positions == 0

    def test_load_corrupt_file_returns_false(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{invalid json!!")
        store = StateStore(path)
        rm = RiskManager(_cfg())
        assert not store.load(rm)

    def test_load_empty_json_object(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("{}")
        store = StateStore(path)
        rm = RiskManager(_cfg())
        assert store.load(rm)
        assert rm.open_positions == 0
        assert rm.session_pnl == 0.0

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "deep" / "state.json"
        store = StateStore(path)
        rm = RiskManager(_cfg())
        store.save(rm)
        assert path.exists()

    def test_clear_removes_file(self, tmp_path):
        path = tmp_path / "state.json"
        store = StateStore(path)
        rm = RiskManager(_cfg())
        store.save(rm)
        assert path.exists()
        store.clear()
        assert not path.exists()

    def test_clear_missing_file_no_error(self, tmp_path):
        store = StateStore(tmp_path / "nope.json")
        store.clear()

    def test_atomic_write_no_partial(self, tmp_path):
        path = tmp_path / "state.json"
        store = StateStore(path)
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        store.save(rm)

        with open(path) as f:
            data = json.load(f)
        assert data["version"] == 1
        assert len(data["positions"]) == 1
        assert not (path.with_suffix(".tmp")).exists()


class TestStateStoreFileFormat:
    def test_json_structure(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60, condition_id="cond1")
        rm.record_pnl(-10.0)
        store.save(rm)

        with open(tmp_path / "state.json") as f:
            data = json.load(f)

        assert "version" in data
        assert "timestamp" in data
        assert data["session_pnl"] == pytest.approx(-10.0)
        assert data["halted"] is False
        assert data["halt_reason"] == ""
        assert len(data["positions"]) == 1

        pos = data["positions"][0]
        assert pos["token_id"] == "tok1"
        assert pos["game"] == "CS2"
        assert pos["team"] == "NAVI"
        assert pos["condition_id"] == "cond1"
        assert pos["amount_usdc"] == 50.0
        assert pos["buy_price"] == 0.60
        assert "timestamp" in pos

        assert len(data["trade_keys"]) == 1
        assert len(data["match_cooldowns"]) == 1
