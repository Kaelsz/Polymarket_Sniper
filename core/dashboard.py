"""
Real-time HTTP Dashboard

Exposes bot state via:
  GET /            — HTML dashboard (auto-refreshes every 5s)
  GET /api/status  — JSON snapshot (positions, PnL, adapters, config)
  GET /api/trades  — Recent trade history
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from core.circuit_breaker import CircuitBreaker
    from core.engine import SniperEngine
    from core.rate_limiter import RateLimiter
    from core.risk import RiskManager

log = logging.getLogger("polysniper.dashboard")

_risk_key: web.AppKey[RiskManager] = web.AppKey("risk")
_cb_key: web.AppKey[CircuitBreaker | None] = web.AppKey("cb")
_engine_key: web.AppKey[SniperEngine] = web.AppKey("engine")
_limiter_key: web.AppKey[RateLimiter | None] = web.AppKey("limiter")

_START_TIME: float = time.time()


def _uptime() -> str:
    s = int(time.time() - _START_TIME)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}h {m}m {s}s"


def _build_status(
    risk: RiskManager,
    cb: CircuitBreaker | None,
    engine: SniperEngine,
    limiter: RateLimiter | None = None,
) -> dict:
    positions = []
    for p in risk._positions:
        positions.append({
            "token_id": p.token_id[:16],
            "game": p.game,
            "team": p.team,
            "match_id": p.match_id,
            "amount_usdc": p.amount_usdc,
            "buy_price": p.buy_price,
            "timestamp": p.timestamp,
        })

    adapters = {}
    if cb:
        for name, health in cb._adapters.items():
            adapters[name] = {
                "state": health.state.value,
                "consecutive_failures": health.consecutive_failures,
                "total_events": health.total_events,
                "total_failures": health.total_failures,
                "last_event_age_s": round(time.time() - health.last_event_time, 1),
            }

    closed = []
    for c in risk._closed_positions:
        closed.append({
            "team": c.team,
            "amount_usdc": c.amount_usdc,
            "buy_price": c.buy_price,
            "exit_price": c.exit_price,
            "pnl": round(c.pnl, 4),
            "source": c.source,
            "opened_at": c.opened_at,
            "closed_at": c.closed_at,
        })

    total_realized = sum(c.pnl for c in risk._closed_positions)
    wins = sum(1 for c in risk._closed_positions if c.pnl > 0)
    losses = sum(1 for c in risk._closed_positions if c.pnl <= 0)

    return {
        "uptime": _uptime(),
        "halted": risk.halted,
        "halt_reason": risk.halt_reason,
        "circuit_breaker_halted": cb.is_halted if cb else False,
        "session_pnl": round(risk.session_pnl, 2),
        "total_realized": round(total_realized, 4),
        "wins": wins,
        "losses": losses,
        "open_positions": risk.open_positions,
        "total_exposure": round(risk.total_exposure, 2),
        "positions": positions,
        "closed_positions": closed,
        "adapters": adapters,
        "trade_count": len(engine._trades),
        "rate_limiter": limiter.stats if limiter else None,
    }


async def _handle_status(request: web.Request) -> web.Response:
    status = _build_status(
        request.app[_risk_key],
        request.app[_cb_key],
        request.app[_engine_key],
        request.app[_limiter_key],
    )
    return web.json_response(status)


async def _handle_trades(request: web.Request) -> web.Response:
    engine: SniperEngine = request.app[_engine_key]
    limit = int(request.query.get("limit", "50"))
    trades = engine._trades[-limit:]
    safe_trades = []
    for t in reversed(trades):
        safe_trades.append({
            "game": t.get("game"),
            "team": t.get("team"),
            "market": t.get("market"),
            "ask_price": t.get("ask_price"),
            "amount": t.get("amount"),
            "latency_ms": t.get("latency_ms"),
            "dry_run": t.get("dry_run"),
            "open_positions": t.get("open_positions"),
            "total_exposure": t.get("total_exposure"),
        })
    return web.json_response(safe_trades)


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PolySniper Dashboard</title>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --text: #e4e4e7; --muted: #8b8d97; --accent: #6366f1;
    --green: #22c55e; --red: #ef4444; --yellow: #eab308;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
    background: var(--bg); color: var(--text); padding: 24px;
    max-width: 1200px; margin: 0 auto;
  }
  header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }
  header h1 { font-size: 1.4rem; font-weight: 600; }
  header h1 span { color: var(--accent); }
  .meta { color: var(--muted); font-size: 0.8rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px;
  }
  .card .label { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
  .card .value { font-size: 1.5rem; font-weight: 700; margin-top: 4px; }
  .positive { color: var(--green); }
  .negative { color: var(--red); }
  .warning { color: var(--yellow); }
  .neutral { color: var(--text); }
  h2 { font-size: 1rem; margin-bottom: 12px; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 24px; }
  th, td {
    text-align: left; padding: 8px 12px; font-size: 0.8rem;
    border-bottom: 1px solid var(--border);
  }
  th { color: var(--muted); font-weight: 500; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 0.7rem; font-weight: 600;
  }
  .badge-ok { background: #16291e; color: var(--green); }
  .badge-err { background: #2d1215; color: var(--red); }
  .badge-warn { background: #2d2712; color: var(--yellow); }
  .empty { color: var(--muted); font-style: italic; padding: 16px; text-align: center; }
  .section { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 24px; }
</style>
</head>
<body>
<header>
  <h1><span>Poly</span>Sniper</h1>
  <div class="meta">
    <span id="uptime"></span> &middot;
    <span id="refresh-indicator">auto-refresh 5s</span>
  </div>
</header>

<div class="grid">
  <div class="card">
    <div class="label">Realized PnL</div>
    <div class="value" id="realized-pnl">$0.00</div>
  </div>
  <div class="card">
    <div class="label">Win / Loss</div>
    <div class="value neutral" id="win-loss">0 / 0</div>
  </div>
  <div class="card">
    <div class="label">Open Positions</div>
    <div class="value neutral" id="positions">0</div>
  </div>
  <div class="card">
    <div class="label">Exposure</div>
    <div class="value neutral" id="exposure">$0.00</div>
  </div>
  <div class="card">
    <div class="label">Status</div>
    <div class="value" id="status">OK</div>
  </div>
</div>

<div class="section">
  <h2>Scanner</h2>
  <table>
    <thead><tr><th>Feed</th><th>State</th><th>Events</th><th>Failures</th><th>Last Event</th></tr></thead>
    <tbody id="adapters-body"><tr><td colspan="5" class="empty">Loading...</td></tr></tbody>
  </table>
</div>

<div class="section">
  <h2>Open Positions</h2>
  <table>
    <thead><tr><th>Market</th><th>Outcome</th><th>Size</th><th>Buy Price</th><th>Age</th></tr></thead>
    <tbody id="positions-body"><tr><td colspan="5" class="empty">No open positions</td></tr></tbody>
  </table>
</div>

<div class="section">
  <h2>Closed Positions (PnL History)</h2>
  <table>
    <thead><tr><th>Market</th><th>Size</th><th>Buy</th><th>Exit</th><th>PnL</th><th>Source</th><th>Duration</th></tr></thead>
    <tbody id="closed-body"><tr><td colspan="7" class="empty">No closed positions yet</td></tr></tbody>
  </table>
</div>

<div class="section">
  <h2>Recent Trades</h2>
  <table>
    <thead><tr><th>Market</th><th>Outcome</th><th>Ask</th><th>Size</th><th>Latency</th></tr></thead>
    <tbody id="trades-body"><tr><td colspan="5" class="empty">No trades yet</td></tr></tbody>
  </table>
</div>

<script>
function pnlClass(v) { return v > 0 ? 'positive' : v < 0 ? 'negative' : 'neutral'; }

function stateBadge(s) {
  if (s === 'CLOSED') return '<span class="badge badge-ok">HEALTHY</span>';
  if (s === 'OPEN') return '<span class="badge badge-err">DOWN</span>';
  return '<span class="badge badge-warn">RECOVERING</span>';
}

function age(ts) {
  const s = Math.floor(Date.now()/1000 - ts);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s/60) + 'm';
  return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
}

async function refresh() {
  try {
    const [statusRes, tradesRes] = await Promise.all([
      fetch('/api/status'), fetch('/api/trades?limit=20')
    ]);
    const st = await statusRes.json();
    const tr = await tradesRes.json();

    document.getElementById('uptime').textContent = st.uptime;
    const rp = document.getElementById('realized-pnl');
    rp.textContent = '$' + (st.total_realized || 0).toFixed(4);
    rp.className = 'value ' + pnlClass(st.total_realized || 0);
    document.getElementById('win-loss').textContent = (st.wins||0) + ' / ' + (st.losses||0);
    document.getElementById('positions').textContent = st.open_positions;
    document.getElementById('exposure').textContent = '$' + st.total_exposure.toFixed(2);

    const halted = st.halted || st.circuit_breaker_halted;
    const statusEl = document.getElementById('status');
    statusEl.textContent = halted ? 'HALTED' : 'RUNNING';
    statusEl.className = 'value ' + (halted ? 'negative' : 'positive');

    // Adapters
    const ab = document.getElementById('adapters-body');
    const adapters = Object.entries(st.adapters);
    if (adapters.length === 0) {
      ab.innerHTML = '<tr><td colspan="5" class="empty">No adapters registered</td></tr>';
    } else {
      ab.innerHTML = adapters.map(([name, a]) =>
        `<tr><td>${name}</td><td>${stateBadge(a.state)}</td>` +
        `<td>${a.total_events}</td><td>${a.total_failures}</td>` +
        `<td>${a.last_event_age_s.toFixed(0)}s ago</td></tr>`
      ).join('');
    }

    // Positions
    const pb = document.getElementById('positions-body');
    if (st.positions.length === 0) {
      pb.innerHTML = '<tr><td colspan="5" class="empty">No open positions</td></tr>';
    } else {
      pb.innerHTML = st.positions.map(p =>
        `<tr><td>${p.game}</td><td>${p.team}</td>` +
        `<td>$${p.amount_usdc.toFixed(2)}</td><td>$${p.buy_price.toFixed(3)}</td>` +
        `<td>${age(p.timestamp)}</td></tr>`
      ).join('');
    }

    // Closed positions
    const cb2 = document.getElementById('closed-body');
    const closed = st.closed_positions || [];
    if (closed.length === 0) {
      cb2.innerHTML = '<tr><td colspan="7" class="empty">No closed positions yet</td></tr>';
    } else {
      cb2.innerHTML = closed.slice().reverse().map(c => {
        const dur = c.closed_at && c.opened_at ? age(c.opened_at).replace(/^/, '') : '?';
        const pClass = c.pnl > 0 ? 'positive' : c.pnl < 0 ? 'negative' : 'neutral';
        return `<tr><td>${c.team}</td><td>$${c.amount_usdc.toFixed(2)}</td>` +
          `<td>$${c.buy_price.toFixed(3)}</td><td>$${c.exit_price.toFixed(3)}</td>` +
          `<td class="${pClass}">$${c.pnl >= 0 ? '+' : ''}${c.pnl.toFixed(4)}</td>` +
          `<td>${c.source || '?'}</td><td>${dur}</td></tr>`;
      }).join('');
    }

    // Trades
    const tb = document.getElementById('trades-body');
    if (tr.length === 0) {
      tb.innerHTML = '<tr><td colspan="5" class="empty">No trades yet</td></tr>';
    } else {
      tb.innerHTML = tr.map(t =>
        `<tr><td>${t.market || t.game}</td><td>${t.team}</td>` +
        `<td>$${t.ask_price.toFixed(3)}</td><td>$${t.amount.toFixed(2)}</td>` +
        `<td>${t.latency_ms}ms</td></tr>`
      ).join('');
    }
  } catch (e) {
    console.error('Refresh error:', e);
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


async def _handle_index(request: web.Request) -> web.Response:
    return web.Response(text=_HTML_TEMPLATE, content_type="text/html")


def create_dashboard_app(
    risk: RiskManager,
    cb: CircuitBreaker | None,
    engine: SniperEngine,
    limiter: RateLimiter | None = None,
) -> web.Application:
    """Create and return the aiohttp dashboard application."""
    global _START_TIME
    _START_TIME = time.time()

    app = web.Application()
    app[_risk_key] = risk
    app[_cb_key] = cb
    app[_engine_key] = engine
    app[_limiter_key] = limiter

    app.router.add_get("/", _handle_index)
    app.router.add_get("/api/status", _handle_status)
    app.router.add_get("/api/trades", _handle_trades)

    return app


async def start_dashboard(
    risk: RiskManager,
    cb: CircuitBreaker | None,
    engine: SniperEngine,
    port: int = 8080,
    limiter: RateLimiter | None = None,
) -> web.AppRunner:
    """Start the dashboard HTTP server as a background task."""
    app = create_dashboard_app(risk, cb, engine, limiter=limiter)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Dashboard running on http://0.0.0.0:%d", port)
    return runner
