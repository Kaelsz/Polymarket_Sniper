"""
End-to-end integration tests.

Real modules wired together:
  FuzzyMapper (real parsing, real rapidfuzz matching)
  RiskManager (real checks, real state)
  CircuitBreaker (real state machine)
  SniperEngine (real event handling)

Only external boundaries are mocked:
  polymarket.get_markets  → fake market catalog
  polymarket.best_ask     → controlled prices
  polymarket.market_buy   → captured call
  send_alert              → captured call
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from adapters.base import BaseAdapter, MatchEvent
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from core.engine import SniperEngine
from core.mapper import FuzzyMapper
from core.risk import RiskConfig, RiskManager


# ---------------------------------------------------------------------------
# Fake market catalog — mirrors what Polymarket would return
# ---------------------------------------------------------------------------
FAKE_MARKETS = [
    {
        "condition_id": "cond_navi_cs2",
        "question": "Will Natus Vincere win CS2 IEM Katowice?",
        "tokens": [
            {"token_id": "tok_navi_yes", "outcome": "Yes"},
            {"token_id": "tok_navi_no", "outcome": "No"},
        ],
    },
    {
        "condition_id": "cond_g2_cs2",
        "question": "Will G2 Esports win CS2 IEM Katowice?",
        "tokens": [
            {"token_id": "tok_g2_yes", "outcome": "Yes"},
            {"token_id": "tok_g2_no", "outcome": "No"},
        ],
    },
    {
        "condition_id": "cond_t1_lol",
        "question": "Will T1 win League of Legends Worlds 2026?",
        "tokens": [
            {"token_id": "tok_t1_yes", "outcome": "Yes"},
            {"token_id": "tok_t1_no", "outcome": "No"},
        ],
    },
    {
        "condition_id": "cond_sen_val",
        "question": "Will Sentinels win Valorant VCT Americas?",
        "tokens": [
            {"token_id": "tok_sen_yes", "outcome": "Yes"},
            {"token_id": "tok_sen_no", "outcome": "No"},
        ],
    },
    {
        "condition_id": "cond_spirit_dota",
        "question": "Will Team Spirit win Dota 2 The International?",
        "tokens": [
            {"token_id": "tok_spirit_yes", "outcome": "Yes"},
            {"token_id": "tok_spirit_no", "outcome": "No"},
        ],
    },
    {
        "condition_id": "cond_liquid_cs2",
        "question": "Will Team Liquid win CS2 BLAST Premier?",
        "tokens": [
            {"token_id": "tok_liquid_yes", "outcome": "Yes"},
            {"token_id": "tok_liquid_no", "outcome": "No"},
        ],
    },
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_polymarket():
    """Mock only the external Polymarket API boundary."""
    mock = AsyncMock()
    mock.get_markets = AsyncMock(return_value=FAKE_MARKETS)
    mock.best_ask = AsyncMock(return_value=0.55)
    mock.market_buy = AsyncMock(return_value=None)
    mock.get_market_resolution = AsyncMock(return_value=None)
    mock.market_sell = AsyncMock(return_value=None)
    return mock


@pytest.fixture
async def loaded_mapper(fake_polymarket):
    """A real FuzzyMapper pre-loaded with fake market data."""
    fm = FuzzyMapper()
    with patch.object(fm, "_FuzzyMapper__class__", None, create=True):
        pass
    # Patch the polymarket dependency inside mapper to return fake data
    with patch("core.mapper.polymarket", fake_polymarket):
        await fm.refresh()
    assert len(fm._markets) == 6
    return fm


@pytest.fixture
def risk_manager():
    return RiskManager(RiskConfig(
        max_open_positions=10,
        max_positions_per_game=4,
        max_session_loss_usdc=200.0,
        max_total_exposure_usdc=500.0,
        match_cooldown_seconds=0.0,
        dedup_window_seconds=300.0,
    ))


@pytest.fixture
def circuit_breaker():
    return CircuitBreaker(CircuitBreakerConfig(
        failure_threshold=2,
        min_healthy_adapters=1,
        stale_data_timeout=999.0,
    ))


@pytest.fixture
def mock_settings():
    """Controlled trading settings."""
    s = AsyncMock()
    s.trading.max_buy_price = 0.85
    s.trading.order_size_usdc = 50.0
    s.trading.dry_run = True
    return s


@pytest.fixture
async def wired_engine(
    fake_polymarket, loaded_mapper, risk_manager, circuit_breaker, mock_settings,
):
    """
    Fully wired engine: real mapper, real risk, real CB.
    Only polymarket API + settings + alerts are mocked.
    """
    queue = asyncio.Queue()
    engine = SniperEngine(queue, risk=risk_manager, circuit_breaker=circuit_breaker)

    patches = {
        "mapper": patch("core.engine.mapper", loaded_mapper),
        "polymarket": patch("core.engine.polymarket", fake_polymarket),
        "settings": patch("core.engine.settings", mock_settings),
        "alert": patch("core.engine.send_alert", new_callable=AsyncMock),
    }

    mocks = {}
    for name, p in patches.items():
        mocks[name] = p.start()

    yield engine, queue, fake_polymarket, mocks["alert"], risk_manager, circuit_breaker

    for p in patches.values():
        p.stop()


# ===================================================================
# HAPPY PATH
# ===================================================================
class TestHappyPath:
    @pytest.mark.asyncio
    async def test_single_cs2_trade(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.55

        event = MatchEvent(
            game="CS2", team_won="NAVI", team_lost="G2",
            event="Match Ended", match_id="hltv_9001",
        )
        await engine._handle_event(event)

        assert len(engine._trades) == 1
        trade = engine._trades[0]
        assert trade["game"] == "CS2"
        assert trade["team"] == "NAVI"
        assert trade["ask_price"] == 0.55
        assert trade["amount"] == 50.0
        assert trade["open_positions"] == 1
        assert trade["total_exposure"] == 50.0
        mock_pm.market_buy.assert_called_once_with("tok_navi_yes", 50.0)
        mock_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_trade_records_in_risk_manager(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.60

        event = MatchEvent(
            game="CS2", team_won="NAVI", event="Match Ended", match_id="m1",
        )
        await engine._handle_event(event)

        assert risk.open_positions == 1
        assert risk.total_exposure == 50.0
        pos = risk._positions[0]
        assert pos.token_id == "tok_navi_yes"
        assert pos.buy_price == 0.60
        assert pos.game == "CS2"


# ===================================================================
# MULTI-GAME PIPELINE
# ===================================================================
class TestMultiGame:
    @pytest.mark.asyncio
    async def test_four_games_four_trades(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        events = [
            MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="cs_1"),
            MatchEvent(game="LoL", team_won="T1", event="Nexus Destroyed", match_id="lol_1"),
            MatchEvent(game="Valorant", team_won="Sentinels", event="Series Won", match_id="val_1"),
            MatchEvent(game="Dota2", team_won="Team Spirit", event="Ancient Destroyed", match_id="dota_1"),
        ]
        for ev in events:
            await engine._handle_event(ev)

        assert len(engine._trades) == 4
        games = [t["game"] for t in engine._trades]
        assert set(games) == {"CS2", "LoL", "Valorant", "Dota2"}
        assert risk.open_positions == 4
        assert risk.total_exposure == 200.0

    @pytest.mark.asyncio
    async def test_correct_token_per_game(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.45

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="a1")
        )
        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="a2")
        )

        buy_calls = mock_pm.market_buy.call_args_list
        tokens_bought = [call[0][0] for call in buy_calls]
        assert "tok_navi_yes" in tokens_bought
        assert "tok_t1_yes" in tokens_bought


# ===================================================================
# ALIAS RESOLUTION END-TO-END
# ===================================================================
class TestAliasResolution:
    @pytest.mark.asyncio
    async def test_navi_alias(self, wired_engine):
        """Adapter sends 'NAVI' → mapper resolves to 'Natus Vincere' market."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="alias1")
        )
        mock_pm.market_buy.assert_called_with("tok_navi_yes", 50.0)

    @pytest.mark.asyncio
    async def test_sen_alias(self, wired_engine):
        """Adapter sends 'SEN' → mapper resolves to 'Sentinels' market."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        await engine._handle_event(
            MatchEvent(game="Valorant", team_won="SEN", event="Series Won", match_id="alias2")
        )
        mock_pm.market_buy.assert_called_with("tok_sen_yes", 50.0)

    @pytest.mark.asyncio
    async def test_spirit_alias(self, wired_engine):
        """Adapter sends 'Spirit' → mapper resolves to 'Team Spirit' market."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        await engine._handle_event(
            MatchEvent(game="Dota2", team_won="Spirit", event="Ancient Destroyed", match_id="alias3")
        )
        mock_pm.market_buy.assert_called_with("tok_spirit_yes", 50.0)

    @pytest.mark.asyncio
    async def test_tl_alias(self, wired_engine):
        """Adapter sends 'TL' → mapper resolves to 'Team Liquid' market."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="TL", event="Match Ended", match_id="alias4")
        )
        mock_pm.market_buy.assert_called_with("tok_liquid_yes", 50.0)


# ===================================================================
# DEDUP, PRICE SKIP, UNKNOWN TEAM
# ===================================================================
class TestFiltering:
    @pytest.mark.asyncio
    async def test_dedup_blocks_second_identical_event(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        event = MatchEvent(
            game="CS2", team_won="NAVI", event="Match Ended", match_id="dedup1"
        )
        await engine._handle_event(event)
        assert len(engine._trades) == 1

        await engine._handle_event(event)
        assert len(engine._trades) == 1  # still 1
        assert mock_pm.market_buy.call_count == 1

    @pytest.mark.asyncio
    async def test_price_too_high_skips(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.92

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="price1")
        )
        assert len(engine._trades) == 0
        mock_pm.market_buy.assert_not_called()

    @pytest.mark.asyncio
    async def test_price_exactly_at_max_executes(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.85

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="price2")
        )
        assert len(engine._trades) == 1

    @pytest.mark.asyncio
    async def test_unknown_team_no_trade(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="Totally Unknown XYZ", event="Match Ended", match_id="unk1")
        )
        assert len(engine._trades) == 0
        mock_pm.best_ask.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_order_book_skips(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = None

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="empty1")
        )
        assert len(engine._trades) == 0


# ===================================================================
# CIRCUIT BREAKER INTEGRATION
# ===================================================================
class TestCircuitBreakerIntegration:
    @pytest.mark.asyncio
    async def test_cb_halt_blocks_trades(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        cb.register("CS2")
        cb.register("LoL")
        # Trigger halt — both adapters fail past threshold
        cb.record_failure("CS2", "err")
        cb.record_failure("CS2", "err")
        cb.record_failure("LoL", "err")
        cb.record_failure("LoL", "err")
        assert cb.is_halted

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="cb1")
        )
        assert len(engine._trades) == 0

    @pytest.mark.asyncio
    async def test_cb_recovery_allows_trades(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        cb.register("CS2")
        cb.record_failure("CS2", "err")
        cb.record_failure("CS2", "err")
        assert cb.is_halted

        # Adapter recovers
        cb.record_success("CS2")
        assert not cb.is_halted

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="cb2")
        )
        assert len(engine._trades) == 1


# ===================================================================
# RISK LIMITS INTEGRATION
# ===================================================================
class TestRiskLimitsIntegration:
    @pytest.mark.asyncio
    async def test_exposure_cap_blocks(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50
        risk._cfg.max_total_exposure_usdc = 100.0

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="exp1")
        )
        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="exp2")
        )
        assert len(engine._trades) == 2
        assert risk.total_exposure == 100.0

        # Third trade would push to $150 → blocked
        await engine._handle_event(
            MatchEvent(game="Valorant", team_won="Sentinels", event="Win", match_id="exp3")
        )
        assert len(engine._trades) == 2

    @pytest.mark.asyncio
    async def test_per_game_limit_blocks(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50
        risk._cfg.max_positions_per_game = 1
        risk._cfg.dedup_window_seconds = 0.0

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="pg1")
        )
        assert len(engine._trades) == 1

        # Same game, different match → blocked by per-game limit
        await engine._handle_event(
            MatchEvent(game="CS2", team_won="G2 Esports", event="Win", match_id="pg2")
        )
        assert len(engine._trades) == 1

        # Different game → allowed
        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="pg3")
        )
        assert len(engine._trades) == 2

    @pytest.mark.asyncio
    async def test_session_loss_halts(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50
        risk._cfg.max_session_loss_usdc = 50.0

        risk.record_pnl(-55.0)
        assert risk.halted

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="loss1")
        )
        assert len(engine._trades) == 0

    @pytest.mark.asyncio
    async def test_close_position_frees_exposure(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50
        risk._cfg.max_total_exposure_usdc = 80.0

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="close1")
        )
        assert risk.total_exposure == 50.0

        # Close that position
        risk.close_position("tok_navi_yes")
        assert risk.total_exposure == 0.0

        # Now a new trade fits
        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="close2")
        )
        assert len(engine._trades) == 2


# ===================================================================
# ADAPTER → QUEUE → ENGINE PIPELINE
# ===================================================================
class StubAdapter(BaseAdapter):
    """Minimal adapter that emits pre-defined events then stops."""

    GAME = "Stub"
    RECONNECT_DELAY = 0.01

    def __init__(self, queue, events: list[MatchEvent], circuit_breaker=None):
        super().__init__(queue, circuit_breaker=circuit_breaker)
        self._events = events

    async def _connect(self) -> None:
        pass

    async def _listen(self) -> None:
        for ev in self._events:
            await self.emit(ev)
        self.stop()


class TestAdapterToEnginePipeline:
    @pytest.mark.asyncio
    async def test_adapter_emits_engine_consumes(
        self, fake_polymarket, loaded_mapper, risk_manager, mock_settings,
    ):
        queue = asyncio.Queue()
        engine = SniperEngine(queue, risk=risk_manager)

        events_to_emit = [
            MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="pipe1"),
            MatchEvent(game="LoL", team_won="T1", event="Nexus Destroyed", match_id="pipe2"),
        ]
        adapter = StubAdapter(queue, events_to_emit)

        with patch("core.engine.mapper", loaded_mapper), \
             patch("core.engine.polymarket", fake_polymarket), \
             patch("core.engine.settings", mock_settings), \
             patch("core.engine.send_alert", new_callable=AsyncMock):
            fake_polymarket.best_ask.return_value = 0.40

            # Run adapter (emits 2 events, then stops)
            await adapter.run()
            assert queue.qsize() == 2

            # Engine processes queue
            ev1 = await queue.get()
            await engine._handle_event(ev1)
            ev2 = await queue.get()
            await engine._handle_event(ev2)

            assert len(engine._trades) == 2
            assert engine._trades[0]["team"] == "NAVI"
            assert engine._trades[1]["team"] == "T1"

    @pytest.mark.asyncio
    async def test_adapter_reports_to_circuit_breaker(
        self, fake_polymarket, loaded_mapper, risk_manager, circuit_breaker, mock_settings,
    ):
        queue = asyncio.Queue()
        cb = circuit_breaker
        cb.register("Stub")

        events_to_emit = [
            MatchEvent(game="CS2", team_won="NAVI", event="Match Ended", match_id="cb_pipe1"),
        ]
        adapter = StubAdapter(queue, events_to_emit, circuit_breaker=cb)
        await adapter.run()

        health = cb.get_health("Stub")
        assert health.total_events == 1
        assert health.consecutive_failures == 0


# ===================================================================
# CONCURRENT EVENTS
# ===================================================================
class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_different_matches_all_execute(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        events = [
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="conc1"),
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="conc2"),
            MatchEvent(game="Valorant", team_won="Sentinels", event="Win", match_id="conc3"),
        ]

        tasks = [engine._handle_event(ev) for ev in events]
        await asyncio.gather(*tasks)

        assert len(engine._trades) == 3
        assert risk.open_positions == 3

    @pytest.mark.asyncio
    async def test_concurrent_same_match_only_one_passes(self, wired_engine):
        """
        Two identical events fired concurrently — the asyncio.Lock ensures
        only one passes through the critical section. Previously this was a
        known race condition; now we assert exactly 1 trade.
        """
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine

        async def slow_ask(token_id):
            await asyncio.sleep(0.02)
            return 0.50

        mock_pm.best_ask = AsyncMock(side_effect=slow_ask)

        event = MatchEvent(
            game="CS2", team_won="NAVI", event="Match Ended", match_id="race1"
        )

        await asyncio.gather(
            engine._handle_event(event),
            engine._handle_event(event),
            engine._handle_event(event),
        )

        assert len(engine._trades) == 1
        assert risk.open_positions == 1
        assert mock_pm.market_buy.call_count == 1


# ===================================================================
# FULL FLOW: ADAPTER FAILURE → CB HALT → RECOVERY → TRADE
# ===================================================================
class TestFullRecoveryFlow:
    @pytest.mark.asyncio
    async def test_failure_halt_recovery_trade(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        cb.register("CS2")
        cb.register("LoL")

        # Phase 1: healthy — trade should work
        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="flow1")
        )
        assert len(engine._trades) == 1

        # Phase 2: adapter failures → halt
        cb.record_failure("CS2", "disconnect")
        cb.record_failure("CS2", "disconnect")
        cb.record_failure("LoL", "timeout")
        cb.record_failure("LoL", "timeout")
        assert cb.is_halted

        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="flow2")
        )
        assert len(engine._trades) == 1  # blocked

        # Phase 3: recovery
        cb.record_success("CS2")
        assert not cb.is_halted

        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="flow3")
        )
        assert len(engine._trades) == 2  # resumed

    @pytest.mark.asyncio
    async def test_risk_halt_via_cb_callback(self):
        """
        Circuit breaker on_halt callback halts the risk manager.
        Verify the full cascade.
        """
        risk = RiskManager(RiskConfig(
            match_cooldown_seconds=0.0,
            dedup_window_seconds=0.0,
        ))

        async def on_halt():
            risk.halt("CB triggered")

        async def on_resume():
            risk.resume()

        cb = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=1, min_healthy_adapters=1),
            on_halt=on_halt,
            on_resume=on_resume,
        )
        cb.register("CS2")

        # Trigger CB
        cb.record_failure("CS2", "err")
        await asyncio.sleep(0.05)
        assert cb.is_halted
        assert risk.halted
        assert "CB triggered" in risk.halt_reason

        # Recover
        cb.record_success("CS2")
        await asyncio.sleep(0.05)
        assert not cb.is_halted
        assert not risk.halted


# ===================================================================
# PNL RESOLUTION INTEGRATION
# ===================================================================
class TestPnlResolution:
    @pytest.mark.asyncio
    async def test_win_resolution_records_pnl(self, wired_engine):
        """Trade → position resolves as WIN → PnL is positive, position closed."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="pnl1")
        )
        assert risk.open_positions == 1
        assert risk.session_pnl == 0.0

        # Simulate market resolution: YES price → $0.98
        mock_pm.best_ask = AsyncMock(return_value=0.98)
        await engine._check_position_resolutions()

        assert risk.open_positions == 0
        assert risk.session_pnl == pytest.approx(50.0)  # bought 100 shares@0.50 → $100 - $50

    @pytest.mark.asyncio
    async def test_loss_resolution_records_negative_pnl(self, wired_engine):
        """Trade → position resolves as LOSS → PnL is negative."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.60

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="pnl2")
        )
        assert risk.open_positions == 1

        mock_pm.best_ask = AsyncMock(return_value=0.02)
        await engine._check_position_resolutions()

        assert risk.open_positions == 0
        assert risk.session_pnl == pytest.approx(-50.0)

    @pytest.mark.asyncio
    async def test_loss_triggers_halt_blocks_next_trade(self, wired_engine):
        """
        Full cascade: trade → loss → session PnL triggers halt → next trade blocked.
        """
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50
        risk._cfg.max_session_loss_usdc = 40.0

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="halt1")
        )
        assert len(engine._trades) == 1

        # Position resolves as LOSS → PnL = -50 > -40 limit → halt
        mock_pm.best_ask = AsyncMock(return_value=0.01)
        await engine._check_position_resolutions()

        assert risk.halted
        assert risk.session_pnl == pytest.approx(-50.0)

        # Next trade is blocked
        mock_pm.best_ask = AsyncMock(return_value=0.40)
        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="halt2")
        )
        assert len(engine._trades) == 1  # still 1

    @pytest.mark.asyncio
    async def test_mixed_resolutions_correct_cumulative_pnl(self, wired_engine):
        """Multiple trades, some win some lose, cumulative PnL tracked correctly."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="mix1")
        )
        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="mix2")
        )
        await engine._handle_event(
            MatchEvent(game="Valorant", team_won="Sentinels", event="Win", match_id="mix3")
        )
        assert risk.open_positions == 3

        prices = {
            "tok_navi_yes": 0.99,  # WIN
            "tok_t1_yes": 0.01,    # LOSS
            "tok_sen_yes": 0.55,   # still open
        }
        mock_pm.best_ask = AsyncMock(side_effect=lambda tid: prices.get(tid))
        await engine._check_position_resolutions()

        assert risk.open_positions == 1
        # NAVI: PnL = +50, T1: PnL = -50, net = 0
        assert risk.session_pnl == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_resolution_frees_exposure_allows_new(self, wired_engine):
        """Position resolves → exposure drops → new trade fits."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50
        risk._cfg.max_total_exposure_usdc = 60.0

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="exp1")
        )
        assert risk.total_exposure == 50.0

        # Second trade would exceed cap → blocked
        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="exp2")
        )
        assert len(engine._trades) == 1

        # NAVI resolves → exposure freed
        mock_pm.best_ask = AsyncMock(return_value=0.99)
        await engine._check_position_resolutions()
        assert risk.total_exposure == 0.0

        # Now T1 trade fits
        mock_pm.best_ask = AsyncMock(return_value=0.50)
        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="exp3")
        )
        assert len(engine._trades) == 2


# ===================================================================
# MARKET BUY ERROR HANDLING (INTEGRATION)
# ===================================================================
class TestMarketBuyFailureIntegration:
    @pytest.mark.asyncio
    async def test_order_failure_no_position_recorded(self, wired_engine):
        """market_buy raises → no position, no exposure, alert sent."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50
        mock_pm.market_buy = AsyncMock(side_effect=ConnectionError("Polygon RPC down"))

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="fail1")
        )

        assert len(engine._trades) == 0
        assert risk.open_positions == 0
        assert risk.total_exposure == 0.0
        mock_alert.assert_called_once()
        assert "Failed" in mock_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_order_failure_allows_retry(self, wired_engine):
        """After a failed order, the same event can be retried successfully."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        call_count = 0

        async def fail_once(token_id, amount):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("Order timed out")
            return None

        mock_pm.market_buy = AsyncMock(side_effect=fail_once)

        event = MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="retry1")

        await engine._handle_event(event)
        assert len(engine._trades) == 0

        await engine._handle_event(event)
        assert len(engine._trades) == 1
        assert risk.open_positions == 1


# ===================================================================
# API RESOLUTION (INTEGRATION)
# ===================================================================
class TestApiResolutionIntegration:
    @pytest.mark.asyncio
    async def test_api_resolution_used_when_available(self, wired_engine):
        """Full flow: trade → API says resolved YES → PnL recorded."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="api1")
        )
        assert risk.open_positions == 1
        pos = risk._positions[0]
        assert pos.condition_id == "cond_navi_cs2"

        mock_pm.get_market_resolution = AsyncMock(return_value="Yes")
        await engine._check_position_resolutions()

        assert risk.open_positions == 0
        assert risk.session_pnl == pytest.approx(50.0)
        assert mock_pm.best_ask.call_count == 1  # only the initial trade ask

    @pytest.mark.asyncio
    async def test_api_unresolved_falls_back_to_price(self, wired_engine):
        """API returns None → fallback to price heuristic."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="fb1")
        )

        mock_pm.get_market_resolution = AsyncMock(return_value=None)
        mock_pm.best_ask = AsyncMock(return_value=0.97)
        await engine._check_position_resolutions()

        assert risk.open_positions == 0
        assert risk.session_pnl == pytest.approx(50.0)


# ===================================================================
# FEES INTEGRATION
# ===================================================================
class TestFeesIntegration:
    @pytest.mark.asyncio
    async def test_win_with_fees_reduces_pnl(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.50
        risk._cfg.fee_rate = 0.02

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="fee1")
        )
        assert risk.open_positions == 1

        mock_pm.best_ask = AsyncMock(return_value=0.99)
        await engine._check_position_resolutions()

        # gross = +50; fees = 50*0.02 = 1.0; net = 49.0
        assert risk.open_positions == 0
        assert risk.session_pnl == pytest.approx(49.0)

    @pytest.mark.asyncio
    async def test_loss_no_fees(self, wired_engine):
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.60
        risk._cfg.fee_rate = 0.02

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="fee2")
        )

        mock_pm.best_ask = AsyncMock(return_value=0.01)
        await engine._check_position_resolutions()

        assert risk.session_pnl == pytest.approx(-50.0)


# ===================================================================
# STOP-LOSS INTEGRATION
# ===================================================================
class TestStopLossIntegration:
    @pytest.mark.asyncio
    async def test_stop_loss_full_flow(self, wired_engine):
        """Trade → price drops → stop-loss triggers → sell → PnL recorded."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.60
        risk._cfg.stop_loss_pct = 0.5

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="sl1")
        )
        assert risk.open_positions == 1

        mock_pm.best_ask = AsyncMock(return_value=0.20)
        mock_pm.market_sell = AsyncMock(return_value=None)
        await engine._check_position_resolutions()

        assert risk.open_positions == 0
        mock_pm.market_sell.assert_called_once()
        assert risk.session_pnl < 0
        assert mock_alert.call_count >= 2  # trade alert + stop-loss alert

    @pytest.mark.asyncio
    async def test_stop_loss_then_next_trade_allowed(self, wired_engine):
        """After stop-loss, exposure is freed and new trades can pass."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.60
        risk._cfg.stop_loss_pct = 0.5
        risk._cfg.max_total_exposure_usdc = 60.0

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="sl2")
        )
        assert risk.total_exposure == 50.0

        # Stop-loss triggers
        mock_pm.best_ask = AsyncMock(return_value=0.20)
        mock_pm.market_sell = AsyncMock(return_value=None)
        await engine._check_position_resolutions()
        assert risk.total_exposure == 0.0

        # New trade fits
        mock_pm.best_ask = AsyncMock(return_value=0.50)
        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="sl3")
        )
        assert len(engine._trades) == 2

    @pytest.mark.asyncio
    async def test_stop_loss_cascade_halt(self, wired_engine):
        """Multiple stop-losses can trigger session loss halt."""
        engine, queue, mock_pm, mock_alert, risk, cb = wired_engine
        mock_pm.best_ask.return_value = 0.60
        risk._cfg.stop_loss_pct = 0.5
        risk._cfg.max_session_loss_usdc = 30.0

        await engine._handle_event(
            MatchEvent(game="CS2", team_won="NAVI", event="Win", match_id="slh1")
        )

        mock_pm.best_ask = AsyncMock(return_value=0.10)
        mock_pm.market_sell = AsyncMock(return_value=None)
        await engine._check_position_resolutions()

        assert risk.halted
        # Next trade blocked
        mock_pm.best_ask = AsyncMock(return_value=0.50)
        await engine._handle_event(
            MatchEvent(game="LoL", team_won="T1", event="Win", match_id="slh2")
        )
        assert len(engine._trades) == 1
