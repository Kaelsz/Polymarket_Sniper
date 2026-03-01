from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.risk import RiskConfig, RiskManager
from core.scanner import Opportunity


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


def _opp(
    token_id: str = "tok_yes",
    condition_id: str = "cond1",
    question: str = "Will X happen?",
    outcome: str = "Yes",
    ask_price: float = 0.97,
    volume: float = 200_000.0,
) -> Opportunity:
    return Opportunity(
        condition_id=condition_id,
        token_id=token_id,
        question=question,
        outcome=outcome,
        ask_price=ask_price,
        volume=volume,
    )


class TestSniperEngineHandleOpportunity:
    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_empty_order_book_skips(self, event_queue):
        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=None)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)

            engine = self._make_engine(event_queue, risk=_make_risk())
            await engine._handle_opportunity(_opp())
            assert len(engine._trades) == 0

    @pytest.mark.asyncio
    async def test_price_too_high_skips(self, event_queue):
        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.995)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99

            engine = self._make_engine(event_queue, risk=_make_risk())
            await engine._handle_opportunity(_opp())
            assert len(engine._trades) == 0

    @pytest.mark.asyncio
    async def test_price_too_low_skips(self, event_queue):
        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.50)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.95
            mock_settings.trading.max_buy_price = 0.99

            engine = self._make_engine(event_queue, risk=_make_risk())
            await engine._handle_opportunity(_opp())
            assert len(engine._trades) == 0

    @pytest.mark.asyncio
    async def test_profitable_trade_executes(self, event_queue):
        risk = _make_risk()

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.market_buy = AsyncMock(return_value={"order_id": "xyz"})
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = False

            engine = self._make_engine(event_queue, risk=risk)
            await engine._handle_opportunity(_opp(token_id="tok_yes_navi"))

            mock_pm.market_buy.assert_called_once_with("tok_yes_navi", 10.0)
            assert len(engine._trades) == 1
            assert engine._trades[0]["open_positions"] == 1
            assert engine._trades[0]["total_exposure"] == 10.0
            mock_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_dry_run_trade_records(self, event_queue):
        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.96)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 25.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=_make_risk())
            await engine._handle_opportunity(_opp())

            assert len(engine._trades) == 1
            assert engine._trades[0]["dry_run"] is True
            assert engine._trades[0]["result"] is None

    @pytest.mark.asyncio
    async def test_edge_price_exactly_at_max(self, event_queue):
        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.99)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=_make_risk())
            await engine._handle_opportunity(_opp())
            assert len(engine._trades) == 1


class TestEngineRiskIntegration:
    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_risk_halt_blocks_trade(self, event_queue):
        risk = _make_risk()
        risk.halt("Test halt")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._handle_opportunity(_opp())

            assert len(engine._trades) == 0
            mock_pm.best_ask.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_dedup_blocks_second_trade(self, event_queue):
        risk = _make_risk(dedup_window_seconds=300.0)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk)

            opp = _opp(token_id="tok1", condition_id="c1")
            await engine._handle_opportunity(opp)
            assert len(engine._trades) == 1

            await engine._handle_opportunity(opp)
            assert len(engine._trades) == 1  # blocked by dedup

    @pytest.mark.asyncio
    async def test_risk_exposure_blocks_trade(self, event_queue):
        risk = _make_risk(max_total_exposure_usdc=60.0)
        risk.record_trade("tok_prev", "market", "Yes", "c0", 50.0, 0.97)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk)
            await engine._handle_opportunity(_opp(token_id="tok2", condition_id="c2"))
            assert len(engine._trades) == 0


class TestTradeLock:
    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_concurrent_same_opp_only_one_passes(self, event_queue):
        risk = _make_risk(dedup_window_seconds=300.0)

        async def slow_buy(token_id, amount):
            await asyncio.sleep(0.05)
            return None

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.market_buy = AsyncMock(side_effect=slow_buy)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk)
            opp = _opp(token_id="tok1", condition_id="race1")

            await asyncio.gather(
                engine._handle_opportunity(opp),
                engine._handle_opportunity(opp),
            )

            assert len(engine._trades) == 1
            assert risk.open_positions == 1

    @pytest.mark.asyncio
    async def test_concurrent_different_opps_both_pass(self, event_queue):
        risk = _make_risk(dedup_window_seconds=300.0)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk)
            opp1 = _opp(token_id="tok1", condition_id="c1")
            opp2 = _opp(token_id="tok2", condition_id="c2")

            await asyncio.gather(
                engine._handle_opportunity(opp1),
                engine._handle_opportunity(opp2),
            )

            assert len(engine._trades) == 2
            assert risk.open_positions == 2


class TestPositionMonitor:
    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_detects_win_resolution(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.50, condition_id="c1")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.get_market_resolution = AsyncMock(return_value="Yes")
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
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.60, condition_id="c1")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.get_market_resolution = AsyncMock(return_value="No")
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
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.50)

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
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.50)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=None)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()
            assert risk.open_positions == 1

    @pytest.mark.asyncio
    async def test_api_error_keeps_position(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.50)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(side_effect=Exception("API down"))

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()
            assert risk.open_positions == 1

    @pytest.mark.asyncio
    async def test_multiple_positions_mixed(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.50, condition_id="c1")
        risk.record_trade("tok2", "market", "Yes", "c2", 50.0, 0.60, condition_id="c2")
        risk.record_trade("tok3", "market", "No", "c3", 50.0, 0.40, condition_id="c3")

        resolutions = {"c1": "Yes", "c2": "No", "c3": None}

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.get_market_resolution = AsyncMock(side_effect=lambda cid: resolutions.get(cid))
            mock_pm.best_ask = AsyncMock(return_value=0.55)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 1
            assert risk._positions[0].token_id == "tok3"
            assert risk.session_pnl == pytest.approx(0.0)
            assert mock_alert.call_count == 2

    @pytest.mark.asyncio
    async def test_loss_resolution_can_trigger_halt(self, event_queue):
        risk = _make_risk(max_session_loss_usdc=40.0)
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.60, condition_id="c1")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.get_market_resolution = AsyncMock(return_value="No")
            mock_pm.best_ask = AsyncMock(return_value=0.50)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.halted
            assert risk.session_pnl == pytest.approx(-50.0)


class TestMarketBuyErrorHandling:
    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_market_buy_exception_no_record(self, event_queue):
        risk = _make_risk()

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.market_buy = AsyncMock(side_effect=Exception("API timeout"))
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = False

            engine = self._make_engine(event_queue, risk=risk)
            await engine._handle_opportunity(_opp())

            assert len(engine._trades) == 0
            assert risk.open_positions == 0
            mock_alert.assert_called_once()
            assert "Failed" in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_market_buy_failure_allows_retry(self, event_queue):
        risk = _make_risk(dedup_window_seconds=300.0)

        call_count = 0

        async def fail_then_succeed(token_id, amount):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Network error")
            return {"order_id": "ok"}

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.market_buy = AsyncMock(side_effect=fail_then_succeed)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = False

            engine = self._make_engine(event_queue, risk=risk)
            opp = _opp(token_id="tok1", condition_id="c1")

            await engine._handle_opportunity(opp)
            assert len(engine._trades) == 0

            await engine._handle_opportunity(opp)
            assert len(engine._trades) == 1
            assert risk.open_positions == 1


class TestApiResolution:
    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_api_resolution_win(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.50, condition_id="cond1")

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
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.60, condition_id="cond1")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.get_market_resolution = AsyncMock(return_value="No")

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            assert risk.session_pnl == pytest.approx(-50.0)

    @pytest.mark.asyncio
    async def test_api_unresolved_keeps_position(self, event_queue):
        """When API says not resolved, position stays open regardless of price."""
        risk = _make_risk()
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.50, condition_id="cond1")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.get_market_resolution = AsyncMock(return_value=None)
            mock_pm.best_ask = AsyncMock(return_value=0.98)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 1
            assert risk.session_pnl == 0.0
            mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_condition_id_keeps_position(self, event_queue):
        """Without condition_id, API check is skipped; price alone does not close."""
        risk = _make_risk()
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.50)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.98)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 1
            mock_pm.get_market_resolution.assert_not_called()


class TestStateSaveOnTrade:
    def _make_engine(self, queue, risk=None, cb=None, state_store=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb, state_store=state_store)

    @pytest.mark.asyncio
    async def test_save_called_after_trade(self, event_queue):
        risk = _make_risk()
        mock_store = MagicMock()

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk, state_store=mock_store)
            await engine._handle_opportunity(_opp())
            mock_store.save.assert_called_once_with(risk)

    @pytest.mark.asyncio
    async def test_save_called_after_resolution(self, event_queue):
        risk = _make_risk()
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.50, condition_id="c1")
        mock_store = MagicMock()

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.get_market_resolution = AsyncMock(return_value="Yes")
            mock_pm.best_ask = AsyncMock(return_value=0.98)

            engine = self._make_engine(event_queue, risk=risk, state_store=mock_store)
            await engine._check_position_resolutions()
            mock_store.save.assert_called_once_with(risk)

    @pytest.mark.asyncio
    async def test_no_save_when_no_store(self, event_queue):
        risk = _make_risk()

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk)
            await engine._handle_opportunity(_opp())
            assert len(engine._trades) == 1

    @pytest.mark.asyncio
    async def test_save_error_does_not_crash(self, event_queue):
        risk = _make_risk()
        mock_store = MagicMock()
        mock_store.save.side_effect = OSError("disk full")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.settings") as mock_settings, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.97)
            mock_pm.market_buy = AsyncMock(return_value=None)
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)
            mock_settings.trading.min_buy_price = 0.0
            mock_settings.trading.max_buy_price = 0.99
            mock_settings.trading.order_size_usdc = 50.0
            mock_settings.trading.dry_run = True

            engine = self._make_engine(event_queue, risk=risk, state_store=mock_store)
            await engine._handle_opportunity(_opp())

            assert len(engine._trades) == 1
            mock_store.save.assert_called_once()


class TestStopLoss:
    def _make_engine(self, queue, risk=None, cb=None):
        from core.engine import SniperEngine
        return SniperEngine(queue, risk=risk, circuit_breaker=cb)

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_on_price_drop(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5)
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.best_ask = AsyncMock(return_value=0.25)
            mock_pm.market_sell = AsyncMock(return_value=None)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            mock_pm.market_sell.assert_called_once()
            shares_sold = mock_pm.market_sell.call_args[0][1]
            assert shares_sold == pytest.approx(50.0 / 0.60)
            mock_alert.assert_called_once()
            assert "STOP-LOSS" in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_stop_loss_pnl_is_negative(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5)
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.20)
            mock_pm.market_sell = AsyncMock(return_value=None)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.session_pnl == pytest.approx(50.0 / 0.60 * 0.20 - 50.0)

    @pytest.mark.asyncio
    async def test_no_stop_loss_when_disabled(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.0)
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.10)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 1
            mock_pm.market_sell = AsyncMock()
            mock_pm.market_sell.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_stop_loss_above_threshold(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5)
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.35)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()
            assert risk.open_positions == 1

    @pytest.mark.asyncio
    async def test_stop_loss_sell_failure_keeps_position(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5)
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.60)

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
    async def test_resolution_takes_priority_over_stop_loss(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5)
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.50, condition_id="c1")

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock) as mock_alert:
            mock_pm.get_market_resolution = AsyncMock(return_value="Yes")
            mock_pm.best_ask = AsyncMock(return_value=0.98)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()

            assert risk.open_positions == 0
            assert risk.session_pnl == pytest.approx(50.0)
            assert "STOP-LOSS" not in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_stop_loss_can_trigger_session_halt(self, event_queue):
        risk = _make_risk(stop_loss_pct=0.5, max_session_loss_usdc=30.0)
        risk.record_trade("tok1", "market", "Yes", "c1", 50.0, 0.60)

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.best_ask = AsyncMock(return_value=0.10)
            mock_pm.market_sell = AsyncMock(return_value=None)

            engine = self._make_engine(event_queue, risk=risk)
            await engine._check_position_resolutions()
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
        cb.register("Scanner")
        cb.record_failure("Scanner", "err")
        assert cb.is_halted

        with patch("core.engine.polymarket") as mock_pm, \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            mock_pm.get_balance_usdc = AsyncMock(return_value=100.0)

            engine = self._make_engine(event_queue, risk=_make_risk(), cb=cb)
            await engine._handle_opportunity(_opp())

            assert len(engine._trades) == 0
            mock_pm.best_ask.assert_not_called()
