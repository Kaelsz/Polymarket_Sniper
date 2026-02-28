from __future__ import annotations

import time

import pytest

from core.risk import RiskClear, RiskConfig, RiskManager, RiskVeto


def _cfg(**overrides) -> RiskConfig:
    defaults = dict(
        max_open_positions=3,
        max_positions_per_game=2,
        max_session_loss_usdc=100.0,
        max_total_exposure_usdc=200.0,
        match_cooldown_seconds=10.0,
        dedup_window_seconds=60.0,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)


class TestPreTradeCheckBasic:
    def test_clear_on_first_trade(self):
        rm = RiskManager(_cfg())
        decision = rm.pre_trade_check("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        assert decision
        assert isinstance(decision, RiskClear)

    def test_clear_different_matches(self):
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        decision = rm.pre_trade_check("tok2", "CS2", "G2", "m2", 50.0, 0.55)
        assert decision


class TestDedup:
    def test_blocks_duplicate_token_while_open(self):
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        decision = rm.pre_trade_check("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        assert not decision
        assert "already holding" in decision.reason.lower()

    def test_blocks_same_market_while_open(self):
        rm = RiskManager(_cfg(match_cooldown_seconds=0.0))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        rm._match_cooldowns["m1"] = time.time() - 1.0
        decision = rm.pre_trade_check("tok2", "CS2", "G2", "m1", 50.0, 0.55)
        assert not decision
        assert "already holding" in decision.reason.lower()

    def test_allows_after_position_closed(self):
        rm = RiskManager(_cfg(dedup_window_seconds=0.0, match_cooldown_seconds=0.0))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        rm.close_position("tok1")
        rm._trade_keys["tok1|m1|navi"] = time.time() - 1.0
        rm._match_cooldowns["m1"] = time.time() - 1.0
        decision = rm.pre_trade_check("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        assert decision


class TestCooldown:
    def test_blocks_same_match_while_position_open(self):
        rm = RiskManager(_cfg(match_cooldown_seconds=10.0))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        decision = rm.pre_trade_check("tok2", "CS2", "G2", "m1", 50.0, 0.55)
        assert not decision

    def test_allows_different_match(self):
        rm = RiskManager(_cfg(match_cooldown_seconds=10.0))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        decision = rm.pre_trade_check("tok2", "CS2", "G2", "m2", 50.0, 0.55)
        assert decision

    def test_blocks_same_match_in_cooldown_after_close(self):
        rm = RiskManager(_cfg(match_cooldown_seconds=9999.0))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        rm.close_position("tok1")
        decision = rm.pre_trade_check("tok2", "CS2", "G2", "m1", 50.0, 0.55)
        assert not decision
        assert "cooldown" in decision.reason.lower()

    def test_allows_after_cooldown_and_close(self):
        rm = RiskManager(_cfg(match_cooldown_seconds=0.0))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        rm.close_position("tok1")
        rm._match_cooldowns["m1"] = time.time() - 1.0
        decision = rm.pre_trade_check("tok2", "CS2", "G2", "m1", 50.0, 0.55)
        assert decision

    def test_no_cooldown_on_empty_match_id(self):
        rm = RiskManager(_cfg(match_cooldown_seconds=10.0))
        rm.record_trade("tok1", "CS2", "NAVI", "", 50.0, 0.60)
        decision = rm.pre_trade_check("tok2", "CS2", "G2", "", 50.0, 0.55)
        # Empty match_id bypasses cooldown
        assert decision or "cooldown" not in getattr(decision, "reason", "")


class TestPositionLimits:
    def test_blocks_at_max_open(self):
        rm = RiskManager(_cfg(max_open_positions=2))
        rm.record_trade("t1", "CS2", "A", "m1", 50.0, 0.6)
        rm.record_trade("t2", "LoL", "B", "m2", 50.0, 0.5)
        decision = rm.pre_trade_check("t3", "Dota2", "C", "m3", 50.0, 0.7)
        assert not decision
        assert "Max open positions" in decision.reason

    def test_blocks_at_max_per_game(self):
        rm = RiskManager(_cfg(max_positions_per_game=1))
        rm.record_trade("t1", "CS2", "A", "m1", 50.0, 0.6)
        decision = rm.pre_trade_check("t2", "CS2", "B", "m2", 50.0, 0.5)
        assert not decision
        assert "CS2" in decision.reason

    def test_different_game_allowed(self):
        rm = RiskManager(_cfg(max_positions_per_game=1))
        rm.record_trade("t1", "CS2", "A", "m1", 50.0, 0.6)
        decision = rm.pre_trade_check("t2", "LoL", "B", "m2", 50.0, 0.5)
        assert decision

    def test_close_position_frees_slot(self):
        rm = RiskManager(_cfg(max_open_positions=1))
        rm.record_trade("t1", "CS2", "A", "m1", 50.0, 0.6)
        rm.close_position("t1")
        decision = rm.pre_trade_check("t2", "CS2", "B", "m2", 50.0, 0.5)
        assert decision


class TestExposure:
    def test_blocks_over_exposure_cap(self):
        rm = RiskManager(_cfg(max_total_exposure_usdc=100.0))
        rm.record_trade("t1", "CS2", "A", "m1", 80.0, 0.6)
        decision = rm.pre_trade_check("t2", "LoL", "B", "m2", 30.0, 0.5)
        assert not decision
        assert "Exposure" in decision.reason

    def test_allows_within_cap(self):
        rm = RiskManager(_cfg(max_total_exposure_usdc=100.0))
        rm.record_trade("t1", "CS2", "A", "m1", 40.0, 0.6)
        decision = rm.pre_trade_check("t2", "LoL", "B", "m2", 40.0, 0.5)
        assert decision


class TestSessionLoss:
    def test_halt_on_session_loss_limit(self):
        rm = RiskManager(_cfg(max_session_loss_usdc=50.0))
        rm.record_pnl(-50.0)
        assert rm.halted
        decision = rm.pre_trade_check("t1", "CS2", "A", "m1", 50.0, 0.6)
        assert not decision
        assert "halted" in decision.reason.lower()

    def test_no_halt_within_limit(self):
        rm = RiskManager(_cfg(max_session_loss_usdc=50.0))
        rm.record_pnl(-30.0)
        assert not rm.halted

    def test_positive_pnl_no_halt(self):
        rm = RiskManager(_cfg(max_session_loss_usdc=50.0))
        rm.record_pnl(100.0)
        assert not rm.halted
        assert rm.session_pnl == 100.0

    def test_cumulative_loss(self):
        rm = RiskManager(_cfg(max_session_loss_usdc=50.0))
        rm.record_pnl(-20.0)
        rm.record_pnl(-15.0)
        assert not rm.halted
        rm.record_pnl(-20.0)  # total = -55
        assert rm.halted


class TestHaltResume:
    def test_external_halt(self):
        rm = RiskManager(_cfg())
        rm.halt("Test halt")
        assert rm.halted
        assert rm.halt_reason == "Test halt"

    def test_resume(self):
        rm = RiskManager(_cfg())
        rm.halt("Test halt")
        rm.resume()
        assert not rm.halted
        assert rm.halt_reason == ""

    def test_resume_when_not_halted(self):
        rm = RiskManager(_cfg())
        rm.resume()
        assert not rm.halted


class TestPositionTracking:
    def test_open_positions_count(self):
        rm = RiskManager(_cfg())
        assert rm.open_positions == 0
        rm.record_trade("t1", "CS2", "A", "m1", 50.0, 0.6)
        assert rm.open_positions == 1
        rm.record_trade("t2", "LoL", "B", "m2", 30.0, 0.5)
        assert rm.open_positions == 2
        rm.close_position("t1")
        assert rm.open_positions == 1

    def test_total_exposure(self):
        rm = RiskManager(_cfg())
        rm.record_trade("t1", "CS2", "A", "m1", 50.0, 0.6)
        rm.record_trade("t2", "LoL", "B", "m2", 30.0, 0.5)
        assert rm.total_exposure == 80.0
        rm.close_position("t1")
        assert rm.total_exposure == 30.0


class TestClosePositionWithPnl:
    def test_win_pnl_positive(self):
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)
        pnl = rm.close_position_with_pnl("tok1", exit_price=1.0)
        # shares = 50/0.50 = 100; value = 100*1.0 = 100; PnL = 100-50 = +50
        assert pnl == pytest.approx(50.0)
        assert rm.session_pnl == pytest.approx(50.0)
        assert rm.open_positions == 0

    def test_loss_pnl_negative(self):
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        pnl = rm.close_position_with_pnl("tok1", exit_price=0.0)
        # shares = 50/0.60 ≈ 83.33; value = 0; PnL = 0-50 = -50
        assert pnl == pytest.approx(-50.0)
        assert rm.session_pnl == pytest.approx(-50.0)
        assert rm.open_positions == 0

    def test_partial_resolution(self):
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.40)
        pnl = rm.close_position_with_pnl("tok1", exit_price=0.70)
        # shares = 50/0.40 = 125; value = 125*0.70 = 87.5; PnL = 87.5-50 = +37.5
        assert pnl == pytest.approx(37.5)

    def test_returns_none_if_not_found(self):
        rm = RiskManager(_cfg())
        pnl = rm.close_position_with_pnl("nonexistent", 1.0)
        assert pnl is None
        assert rm.session_pnl == 0.0

    def test_cumulative_pnl_triggers_halt(self):
        rm = RiskManager(_cfg(max_session_loss_usdc=40.0))
        rm.record_trade("tok1", "CS2", "A", "m1", 50.0, 0.60)
        rm.record_trade("tok2", "LoL", "B", "m2", 50.0, 0.50)
        rm.close_position_with_pnl("tok1", 0.0)  # PnL = -50
        assert rm.halted
        assert rm.session_pnl == pytest.approx(-50.0)

    def test_win_then_loss_cumulative(self):
        rm = RiskManager(_cfg(max_session_loss_usdc=100.0))
        rm.record_trade("tok1", "CS2", "A", "m1", 50.0, 0.50)
        rm.record_trade("tok2", "LoL", "B", "m2", 50.0, 0.60)
        rm.close_position_with_pnl("tok1", 1.0)  # PnL = +50
        assert rm.session_pnl == pytest.approx(50.0)
        rm.close_position_with_pnl("tok2", 0.0)  # PnL = -50
        assert rm.session_pnl == pytest.approx(0.0)
        assert not rm.halted

    def test_frees_exposure(self):
        rm = RiskManager(_cfg(max_total_exposure_usdc=100.0))
        rm.record_trade("tok1", "CS2", "A", "m1", 80.0, 0.60)
        assert rm.total_exposure == 80.0
        rm.close_position_with_pnl("tok1", 1.0)
        assert rm.total_exposure == 0.0
        # New trade fits now
        decision = rm.pre_trade_check("tok2", "LoL", "B", "m2", 80.0, 0.50)
        assert decision


class TestFees:
    def test_fee_deducted_on_win(self):
        rm = RiskManager(_cfg(fee_rate=0.02))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)
        pnl = rm.close_position_with_pnl("tok1", 1.0)
        # gross = 100*1.0 - 50 = +50; fees = 50*0.02 = 1.0; net = 49.0
        assert pnl == pytest.approx(49.0)
        assert rm.session_pnl == pytest.approx(49.0)

    def test_no_fee_on_loss(self):
        rm = RiskManager(_cfg(fee_rate=0.02))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        pnl = rm.close_position_with_pnl("tok1", 0.0)
        # gross = -50; no fee on losses; net = -50
        assert pnl == pytest.approx(-50.0)

    def test_no_fee_when_rate_zero(self):
        rm = RiskManager(_cfg(fee_rate=0.0))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)
        pnl = rm.close_position_with_pnl("tok1", 1.0)
        assert pnl == pytest.approx(50.0)

    def test_no_fee_when_apply_fees_false(self):
        rm = RiskManager(_cfg(fee_rate=0.10))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)
        pnl = rm.close_position_with_pnl("tok1", 1.0, apply_fees=False)
        # gross = +50; apply_fees=False → no deduction
        assert pnl == pytest.approx(50.0)

    def test_high_fee_rate(self):
        rm = RiskManager(_cfg(fee_rate=0.10))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 100.0, 0.50)
        pnl = rm.close_position_with_pnl("tok1", 1.0)
        # gross = 200*1.0 - 100 = +100; fees = 100*0.10 = 10; net = 90
        assert pnl == pytest.approx(90.0)

    def test_fee_on_partial_resolution_profit(self):
        rm = RiskManager(_cfg(fee_rate=0.02))
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.40)
        pnl = rm.close_position_with_pnl("tok1", 0.70)
        # gross = 125*0.70 - 50 = 37.5; fees = 37.5*0.02 = 0.75; net = 36.75
        assert pnl == pytest.approx(36.75)

    def test_fee_impacts_session_loss_halt(self):
        """Fees reduce profit, making the session loss limit hit sooner."""
        rm = RiskManager(_cfg(fee_rate=0.50, max_session_loss_usdc=10.0))
        rm.record_trade("tok1", "CS2", "A", "m1", 50.0, 0.50)
        rm.record_trade("tok2", "LoL", "B", "m2", 50.0, 0.60)
        rm.close_position_with_pnl("tok1", 1.0)
        # gross = +50; fees = 25; net = +25
        assert rm.session_pnl == pytest.approx(25.0)
        rm.close_position_with_pnl("tok2", 0.0)
        # gross = -50; no fee; cumulative = 25 - 50 = -25
        assert rm.session_pnl == pytest.approx(-25.0)
        assert rm.halted


class TestStateSerialization:
    def test_round_trip_empty(self):
        rm = RiskManager(_cfg())
        state = rm.to_state_dict()
        rm2 = RiskManager(_cfg())
        rm2.load_state_dict(state)
        assert rm2.open_positions == 0
        assert rm2.session_pnl == 0.0
        assert not rm2.halted

    def test_round_trip_with_positions(self):
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60, condition_id="cond1")
        rm.record_pnl(10.0)
        state = rm.to_state_dict()

        rm2 = RiskManager(_cfg())
        rm2.load_state_dict(state)
        assert rm2.open_positions == 1
        assert rm2.session_pnl == pytest.approx(10.0)
        assert rm2._positions[0].condition_id == "cond1"
        assert rm2.total_exposure == pytest.approx(50.0)

    def test_round_trip_preserves_dedup(self):
        rm = RiskManager(_cfg())
        rm.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        state = rm.to_state_dict()

        rm2 = RiskManager(_cfg())
        rm2.load_state_dict(state)
        decision = rm2.pre_trade_check("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)
        assert not decision

    def test_round_trip_preserves_halt(self):
        rm = RiskManager(_cfg())
        rm.halt("test halt")
        state = rm.to_state_dict()

        rm2 = RiskManager(_cfg())
        rm2.load_state_dict(state)
        assert rm2.halted
        assert rm2.halt_reason == "test halt"

    def test_state_dict_version(self):
        rm = RiskManager(_cfg())
        state = rm.to_state_dict()
        assert state["version"] == 1
        assert "timestamp" in state


class TestRiskDecisionTypes:
    def test_veto_is_falsy(self):
        v = RiskVeto("blocked")
        assert not v
        assert v.reason == "blocked"

    def test_clear_is_truthy(self):
        c = RiskClear()
        assert c

    def test_veto_repr(self):
        v = RiskVeto("test")
        assert "test" in repr(v)

    def test_clear_repr(self):
        c = RiskClear()
        assert "Clear" in repr(c)
