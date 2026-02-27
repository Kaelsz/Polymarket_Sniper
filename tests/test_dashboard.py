"""Tests for the real-time HTTP dashboard."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from core.dashboard import create_dashboard_app, _build_status
from core.engine import SniperEngine
from core.risk import RiskConfig, RiskManager


@pytest.fixture
def risk():
    return RiskManager(RiskConfig(
        max_open_positions=10,
        max_total_exposure_usdc=500.0,
        fee_rate=0.02,
    ))


@pytest.fixture
def cb():
    breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))
    breaker.register("Scanner")
    return breaker


@pytest.fixture
def engine(risk):
    queue = asyncio.Queue()
    return SniperEngine(queue, risk=risk)


@pytest.fixture
def dashboard_app(risk, cb, engine):
    return create_dashboard_app(risk, cb, engine)


@pytest.fixture
async def client(dashboard_app):
    server = TestServer(dashboard_app)
    cli = TestClient(server)
    await cli.start_server()
    yield cli
    await cli.close()


# ------------------------------------------------------------------
# API /api/status
# ------------------------------------------------------------------
class TestApiStatus:
    async def test_status_returns_200(self, client):
        resp = await client.get("/api/status")
        assert resp.status == 200

    async def test_status_json_structure(self, client):
        resp = await client.get("/api/status")
        data = await resp.json()
        assert "uptime" in data
        assert "halted" in data
        assert "session_pnl" in data
        assert "open_positions" in data
        assert "total_exposure" in data
        assert "positions" in data
        assert "adapters" in data
        assert "trade_count" in data
        assert "circuit_breaker_halted" in data

    async def test_status_initial_values(self, client):
        resp = await client.get("/api/status")
        data = await resp.json()
        assert data["halted"] is False
        assert data["session_pnl"] == 0.0
        assert data["open_positions"] == 0
        assert data["total_exposure"] == 0.0
        assert data["positions"] == []
        assert data["trade_count"] == 0

    async def test_status_shows_adapters(self, client):
        resp = await client.get("/api/status")
        data = await resp.json()
        assert "Scanner" in data["adapters"]
        assert data["adapters"]["Scanner"]["state"] == "CLOSED"

    async def test_status_reflects_positions(self, client, risk):
        risk.record_trade(
            token_id="tok123",
            game="market",
            team="Yes",
            match_id="c1",
            amount_usdc=50.0,
            buy_price=0.60,
        )
        resp = await client.get("/api/status")
        data = await resp.json()
        assert data["open_positions"] == 1
        assert data["total_exposure"] == 50.0
        assert len(data["positions"]) == 1
        assert data["positions"][0]["game"] == "market"
        assert data["positions"][0]["team"] == "Yes"

    async def test_status_reflects_pnl(self, client, risk):
        risk.record_pnl(-25.0)
        resp = await client.get("/api/status")
        data = await resp.json()
        assert data["session_pnl"] == -25.0

    async def test_status_reflects_halt(self, client, risk):
        risk.halt("Test halt")
        resp = await client.get("/api/status")
        data = await resp.json()
        assert data["halted"] is True
        assert data["halt_reason"] == "Test halt"

    async def test_status_reflects_cb_halt(self, client, cb):
        for _ in range(3):
            cb.record_failure("Scanner", "err")
        resp = await client.get("/api/status")
        data = await resp.json()
        assert data["circuit_breaker_halted"] is True


# ------------------------------------------------------------------
# API /api/trades
# ------------------------------------------------------------------
class TestApiTrades:
    async def test_trades_returns_200(self, client):
        resp = await client.get("/api/trades")
        assert resp.status == 200

    async def test_trades_empty_initially(self, client):
        resp = await client.get("/api/trades")
        data = await resp.json()
        assert data == []

    async def test_trades_returns_recorded_trades(self, client, engine):
        engine._trades.append({
            "game": "market",
            "team": "Yes",
            "market": "Will X happen?",
            "ask_price": 0.65,
            "amount": 50.0,
            "latency_ms": 42.3,
            "dry_run": True,
            "open_positions": 1,
            "total_exposure": 50.0,
        })
        resp = await client.get("/api/trades")
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["game"] == "market"
        assert data[0]["team"] == "Yes"
        assert data[0]["latency_ms"] == 42.3

    async def test_trades_limit_parameter(self, client, engine):
        for i in range(10):
            engine._trades.append({
                "game": "market", "team": f"Team{i}", "market": "q",
                "ask_price": 0.5, "amount": 10.0, "latency_ms": 1.0,
                "dry_run": True, "open_positions": i, "total_exposure": 10.0,
            })
        resp = await client.get("/api/trades?limit=3")
        data = await resp.json()
        assert len(data) == 3

    async def test_trades_most_recent_first(self, client, engine):
        engine._trades.append({
            "game": "market", "team": "First", "market": "q",
            "ask_price": 0.5, "amount": 10.0, "latency_ms": 1.0,
            "dry_run": True, "open_positions": 1, "total_exposure": 10.0,
        })
        engine._trades.append({
            "game": "market", "team": "Last", "market": "q",
            "ask_price": 0.5, "amount": 10.0, "latency_ms": 1.0,
            "dry_run": True, "open_positions": 2, "total_exposure": 20.0,
        })
        resp = await client.get("/api/trades")
        data = await resp.json()
        assert data[0]["team"] == "Last"
        assert data[1]["team"] == "First"


# ------------------------------------------------------------------
# HTML Dashboard
# ------------------------------------------------------------------
class TestHtmlDashboard:
    async def test_index_returns_html(self, client):
        resp = await client.get("/")
        assert resp.status == 200
        assert "text/html" in resp.content_type
        text = await resp.text()
        assert "PolySniper" in text

    async def test_index_contains_api_calls(self, client):
        resp = await client.get("/")
        text = await resp.text()
        assert "/api/status" in text
        assert "/api/trades" in text


# ------------------------------------------------------------------
# _build_status unit test (no HTTP)
# ------------------------------------------------------------------
class TestBuildStatus:
    def test_build_status_no_cb(self, risk, engine):
        status = _build_status(risk, None, engine)
        assert status["circuit_breaker_halted"] is False
        assert status["adapters"] == {}

    def test_build_status_with_positions(self, risk, cb, engine):
        risk.record_trade(
            token_id="tok_abc",
            game="Valorant",
            team="SEN",
            match_id="m5",
            amount_usdc=100.0,
            buy_price=0.70,
        )
        status = _build_status(risk, cb, engine)
        assert status["open_positions"] == 1
        assert status["total_exposure"] == 100.0
        assert len(status["positions"]) == 1
        pos = status["positions"][0]
        assert pos["game"] == "Valorant"
        assert pos["team"] == "SEN"
        assert pos["amount_usdc"] == 100.0
        assert pos["buy_price"] == 0.70

    def test_build_status_adapter_info(self, risk, cb, engine):
        cb.record_success("Scanner")
        status = _build_status(risk, cb, engine)
        assert status["adapters"]["Scanner"]["total_events"] == 1
