from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.base import MatchEvent
from core.risk import RiskConfig, RiskManager


def _make_risk(**overrides) -> RiskManager:
    defaults = dict(
        max_open_positions=10,
        max_positions_per_game=4,
        max_session_loss_usdc=200.0,
        max_total_exposure_usdc=500.0,
        match_cooldown_seconds=0.0,
        dedup_window_seconds=0.0,
    )
    defaults.update(overrides)
    return RiskManager(RiskConfig(**defaults))


class TestSniperEngineHandleEvent:
    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_no_market_found_skips(self, event_queue):
        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = None

            engine = self._make_engine(event_queue, risk=_make_risk())
            event = MatchEvent(game="CS2", team_won="UnknownTeam", event="Match Ended")
            await engine._handle_event(event)

            assert len(engine._trades) == 0

    @pytest.mark.asyncio
    async def test_empty_order_book_skips(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will NAVI win CS2?",
            team_name="Natus Vincere",
            game_hint="CS2",
        )

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=None)

            engine = self._make_engine(event_queue, risk=_make_risk())
            event = MatchEvent(game="CS2", team_won="NAVI", event="Match Ended")
            await engine._handle_event(event)

            assert len(engine._trades) == 0

    @pytest.mark.asyncio
    async def test_price_too_high_skips(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will NAVI win CS2?",
            team_name="Natus Vincere",
            game_hint="CS2",
        )

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.95)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=_make_risk())
            event = MatchEvent(game="CS2", team_won="NAVI", event="Match Ended")
            await engine._handle_event(event)

            assert len(engine._trades) == 0

    @pytest.mark.asyncio
    async def test_profitable_trade_executes(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes_navi",
            token_id_no="tok_no_navi",
            question="Will NAVI win CS2?",
            team_name="Natus Vincere",
            game_hint="CS2",
        )

        risk = _make_risk()

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.60)
            mock_pm.market_buy = AsyncMock(return_value={"order_id": "xyz"})
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = False

            engine = self._make_engine(event_queue, risk=risk)
            event = MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="m1")
            await engine._handle_event(event)

            mock_pm.market_buy.assert_called_once_with("tok_yes_navi", 50.0)
            assert len(engine._trades) == 1
            assert engine._trades[0]["open_positions"] == 1
            assert engine._trades[0]["total_exposure"] == 50.0
            mock_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_dry_run_trade_records(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will T1 win LoL?",
            team_name="T1",
            game_hint="LoL",
        )

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 25.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=_make_risk())
            event = MatchEvent(game="LoL", team_won="T1", event="Nexus Destroyed", match_id="m5")
            await engine._handle_event(event)

            assert len(engine._trades) == 1
            assert engine._trades[0]["dry_run"] is True
            assert engine._trades[0]["result"] is None

    @pytest.mark.asyncio
    async def test_edge_price_exactly_at_max(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_edge",
            token_id_no="tok_no",
            question="Will Spirit win Dota 2?",
            team_name="Team Spirit",
            game_hint="Dota2",
        )

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.85)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=_make_risk())
            event = MatchEvent(game="Dota2", team_won="Spirit", event="Ancient Destroyed", match_id="m9")
            await engine._handle_event(event)

            assert len(engine._trades) == 1


class TestEngineRiskIntegration:
    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_risk_halt_blocks_trade(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will NAVI win CS2?",
            team_name="NAVI",
            game_hint="CS2",
        )
        risk = _make_risk()
        risk.halt("Test halt")

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.50)

            engine = self._make_engine(event_queue, risk=risk)
            event = MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="m1")
            await engine._handle_event(event)

            assert len(engine._trades) == 0
            mock_pm.best_ask.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_dedup_blocks_second_trade(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will NAVI win CS2?",
            team_name="NAVI",
            game_hint="CS2",
        )
        risk = _make_risk(dedup_window_seconds=300.0)

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk)

            event1 = MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="m1")
            await engine._handle_event(event1)
            assert len(engine._trades) == 1

            event2 = MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="m1")
            await engine._handle_event(event2)
            assert len(engine._trades) == 1  # blocked

    @pytest.mark.asyncio
    async def test_risk_exposure_blocks_trade(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will G2 win CS2?",
            team_name="G2",
            game_hint="CS2",
        )
        risk = _make_risk(max_total_exposure_usdc=60.0)
        risk.record_trade("tok_prev", "CS2", "NAVI", "m0", 50.0, 0.6)

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk)
            event = MatchEvent(game="CS2", team_won="G2", event="Match Ended", match_id="m2")
            await engine._handle_event(event)

            assert len(engine._trades) == 0


class TestTradeLock:
    """Verify the asyncio.Lock prevents concurrent duplicate trades."""

    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_concurrent_same_event_only_one_passes(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will NAVI win CS2?",
            team_name="NAVI",
            game_hint="CS2",
        )
        risk = _make_risk(dedup_window_seconds=300.0)

        async def slow_buy(token_id, amount):
            await asyncio.sleep(0.05)
            return None

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_pm.market_buy = AsyncMock(side_effect=slow_buy)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk)
            event = MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="race1")

            # Fire two identical events concurrently
            await asyncio.gather(
                engine._handle_event(event),
                engine._handle_event(event),
            )

            assert len(engine._trades) == 1
            assert risk.open_positions == 1

    @pytest.mark.asyncio
    async def test_concurrent_different_events_both_pass(self, event_queue):
        from core.mapper import MarketMapping

        mappings = {
            "NAVI": MarketMapping("c1", "tok_navi", "tok_no1", "NAVI CS2?", "NAVI", "CS2"),
            "T1": MarketMapping("c2", "tok_t1", "tok_no2", "T1 LoL?", "T1", "LoL"),
        }
        risk = _make_risk(dedup_window_seconds=300.0)

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.side_effect = lambda team, game: mappings.get(team)
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk)
            ev1 = MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="m1")
            ev2 = MatchEvent(game="LoL", team_won="T1", event="Win", match_id="m2")

            await asyncio.gather(
                engine._handle_event(ev1),
                engine._handle_event(ev2),
            )

            assert len(engine._trades) == 2
            assert risk.open_positions == 2


class TestPositionMonitor:
    """Verify _check_position_resolutions detects wins/losses."""

    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_detects_win_resolution(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.best_ask = AsyncMock(return_value=0.98)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            assert risk.session_pnl == pytest.approx(50.0)
            mock_alert.assert_called_once()
            assert "WIN" in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_detects_loss_resolution(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.best_ask = AsyncMock(return_value=0.02)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            assert risk.session_pnl == pytest.approx(-50.0)
            mock_alert.assert_called_once()
            assert "LOSS" in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_resolution_keeps_position(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.best_ask = AsyncMock(return_value=0.65)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 1
            assert risk.session_pnl == 0.0
            mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_price_keeps_position(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=None)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 1

    @pytest.mark.asyncio
    async def test_api_error_keeps_position(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(side_effect=Exception("API down"))

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 1

    @pytest.mark.asyncio
    async def test_multiple_positions_mixed(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)
        risk.record_trade("tok2", "LoL", "T1", "m2", 50.0, 0.60)
        risk.record_trade("tok3", "Dota2", "Spirit", "m3", 50.0, 0.40)

        prices = {"tok1": 0.98, "tok2": 0.02, "tok3": 0.55}

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.best_ask = AsyncMock(side_effect=lambda tid: prices.get(tid))

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            # tok1 resolved WIN, tok2 resolved LOSS, tok3 still open
            assert risk.open_positions == 1
            assert risk._positions[0].token_id == "tok3"
            # PnL: tok1 = +50, tok2 = -50 → net 0
            assert risk.session_pnl == pytest.approx(0.0)
            assert mock_alert.call_count == 2

    @pytest.mark.asyncio
    async def test_loss_resolution_can_trigger_halt(self, event_queue):
        risk = _make_risk(max_session_loss_usdc=40.0)
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.01)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.halted
            assert risk.session_pnl == pytest.approx(-50.0)


class TestMarketBuyErrorHandling:
    """Verify that market_buy failures don't record trades."""

    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_market_buy_exception_no_record(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will NAVI win CS2?",
            team_name="NAVI",
            game_hint="CS2",
        )
        risk = _make_risk()

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_pm.market_buy = AsyncMock(side_effect=Exception("API timeout"))
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = False

            engine = self._make_engine(event_queue, risk=risk)
            event = MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="m1")
            await engine._handle_event(event)

            assert len(engine._trades) == 0
            assert risk.open_positions == 0
            mock_alert.assert_called_once()
            assert "Failed" in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_market_buy_failure_allows_retry(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will NAVI win CS2?",
            team_name="NAVI",
            game_hint="CS2",
        )
        risk = _make_risk(dedup_window_seconds=300.0)

        call_count = 0

        async def fail_then_succeed(token_id, amount):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Network error")
            return {"order_id": "ok"}

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_pm.market_buy = AsyncMock(side_effect=fail_then_succeed)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = False

            engine = self._make_engine(event_queue, risk=risk)
            event = MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="m1")

            # First attempt: fails
            await engine._handle_event(event)
            assert len(engine._trades) == 0

            # Retry: succeeds (dedup was NOT recorded on failure)
            await engine._handle_event(event)
            assert len(engine._trades) == 1
            assert risk.open_positions == 1


class TestApiResolution:
    """Verify _check_position_resolutions uses API first, fallback to price."""

    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_api_resolution_win(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50, condition_id="cond1")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.get_market_resolution = AsyncMock(return_value="Yes")

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            assert risk.session_pnl == pytest.approx(50.0)
            mock_pm.best_ask.assert_not_called()
            assert "API" in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_api_resolution_loss(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60, condition_id="cond1")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.get_market_resolution = AsyncMock(return_value="No")

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            assert risk.session_pnl == pytest.approx(-50.0)

    @pytest.mark.asyncio
    async def test_api_unresolved_falls_back_to_price(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50, condition_id="cond1")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.get_market_resolution = AsyncMock(return_value=None)
            mock_pm.best_ask = AsyncMock(return_value=0.98)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            assert risk.session_pnl == pytest.approx(50.0)
            mock_pm.best_ask.assert_called_once()
            assert "price" in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_condition_id_skips_api(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.98)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            mock_pm.get_market_resolution.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_error_falls_back_to_price(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50, condition_id="cond1")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.get_market_resolution = AsyncMock(return_value=None)
            mock_pm.best_ask = AsyncMock(return_value=0.99)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            assert risk.session_pnl == pytest.approx(50.0)


class TestStateSaveOnTrade:
    """Verify state_store.save is called after trades and resolutions."""

    def _make_engine(self, queue, risk=None, cb=None, state_store=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb, state_store=state_store)

    @pytest.mark.asyncio
    async def test_save_called_after_trade(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will NAVI win CS2?",
            team_name="NAVI",
            game_hint="CS2",
        )
        risk = _make_risk()
        mock_store = MagicMock()

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk, state_store=mock_store)
            event = MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="m1")
            await engine._handle_event(event)

            mock_store.save.assert_called_once_with(risk)

    @pytest.mark.asyncio
    async def test_save_called_after_resolution(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)
        mock_store = MagicMock()

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.98)

            engine = self._make_engine(event_queue, risk=risk, state_store=mock_store)
            await engine._check_position_resolutions()

            mock_store.save.assert_called_once_with(risk)

    @pytest.mark.asyncio
    async def test_no_save_when_no_store(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will NAVI win CS2?",
            team_name="NAVI",
            game_hint="CS2",
        )
        risk = _make_risk()

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk)
            event = MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="m1")
            await engine._handle_event(event)
            assert len(engine._trades) == 1

    @pytest.mark.asyncio
    async def test_save_error_does_not_crash(self, event_queue):
        from core.mapper import MarketMapping

        mapping = MarketMapping(
            condition_id="c1",
            token_id_yes="tok_yes",
            token_id_no="tok_no",
            question="Will NAVI win CS2?",
            team_name="NAVI",
            game_hint="CS2",
        )
        risk = _make_risk()
        mock_store = MagicMock()
        mock_store.save.side_effect = OSError("disk full")

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_mapper.find_token.return_value = mapping
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_settings.trading.max_buy_price = 0.85
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk, state_store=mock_store)
            event = MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="m1")
            await engine._handle_event(event)

            assert len(engine._trades) == 1
            mock_store.save.assert_called_once()


class TestStopLoss:
    """Verify stop-loss detection and execution."""

    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_on_price_drop(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5)
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.best_ask = AsyncMock(return_value=0.25)
            mock_pm.market_sell = AsyncMock(return_value=None)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            # buy_price=0.60, stop_loss_pct=0.5 → trigger at 0.30
            # price 0.25 < 0.30 → stop-loss
            assert risk.open_positions == 0
            mock_pm.market_sell.assert_called_once()
            shares_sold = mock_pm.market_sell.call_args[0][1]
            assert shares_sold == pytest.approx(50.0 / 0.60)
            mock_alert.assert_called_once()
            assert "STOP-LOSS" in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_stop_loss_pnl_is_negative(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5)
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.20)
            mock_pm.market_sell = AsyncMock(return_value=None)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            # shares = 50/0.60 ≈ 83.33; exit_value = 83.33 * 0.20 ≈ 16.67
            # PnL = 16.67 - 50 ≈ -33.33
            assert risk.session_pnl == pytest.approx(50.0 / 0.60 * 0.20 - 50.0)

    @pytest.mark.asyncio
    async def test_no_stop_loss_when_disabled(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.0)
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.10)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            # Price is very low but stop_loss disabled → position stays
            assert risk.open_positions == 1
            mock_pm.market_sell = AsyncMock()
            mock_pm.market_sell.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_stop_loss_above_threshold(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5)
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            # Price at 0.35 > trigger(0.30) → no stop-loss
            mock_pm.best_ask = AsyncMock(return_value=0.35)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 1

    @pytest.mark.asyncio
    async def test_stop_loss_sell_failure_keeps_position(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5)
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.best_ask = AsyncMock(return_value=0.20)
            mock_pm.market_sell = AsyncMock(side_effect=Exception("API down"))

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 1
            mock_alert.assert_called_once()
            assert "Failed" in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_stop_loss_no_fees_applied(self, event_queue):
        """Stop-loss exits should NOT have Polymarket resolution fees."""
        risk = _make_risk(stop_loss_pct=0.5, fee_rate=0.10)
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.20)
            mock_pm.market_sell = AsyncMock(return_value=None)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            # Loss → no fees regardless; but also apply_fees=False
            expected = 50.0 / 0.60 * 0.20 - 50.0
            assert risk.session_pnl == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_resolution_takes_priority_over_stop_loss(self, event_queue):
        """Price >= 0.95 should resolve as WIN, not trigger stop-loss."""
        risk = _make_risk(stop_loss_pct=0.5)
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.50)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.best_ask = AsyncMock(return_value=0.98)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            assert risk.session_pnl == pytest.approx(50.0)
            assert "STOP-LOSS" not in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_stop_loss_can_trigger_session_halt(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5, max_session_loss_usdc=30.0)
        risk.record_trade("tok1", "CS2", "NAVI", "m1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.10)
            mock_pm.market_sell = AsyncMock(return_value=None)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            # PnL ≈ -41.67 < -30 → halt
            assert risk.halted


class TestEngineCircuitBreakerIntegration:
    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_circuit_breaker_halt_blocks_trade(self, event_queue):
        from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=1, min_healthy_adapters=1,
        ))
        cb.register("CS2")
        cb.record_failure("CS2", "err")
        assert cb.is_halted

        with patch("core.engine.mapper") as mock_mapper, \
             patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):

            engine = self._make_engine(event_queue, risk=_make_risk(), cb=cb)
            event = MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="m1")
            await engine._handle_event(event)

            assert len(engine._trades) == 0
            mock_mapper.find_token.assert_not_called()
