# PolySniper v2.0

High-frequency resolution sniper for [Polymarket](https://polymarket.com). Scans all active markets in real time, identifies near-certain outcomes (price $0.95–$0.99), and automatically buys outcome tokens that resolve to $1.00 — capturing the spread as profit.

## How It Works

```
    Gamma API (market discovery, every 30s)
                │
                ▼
    ┌───────────────────────┐
    │    MarketScanner      │──── filters by volume, end date, price
    │  (33K+ markets/scan)  │
    └───────┬───────────────┘
            │                          ┌──────────────────────────┐
            │ eligible markets         │   Polymarket WebSocket   │
            ├─────────────────────────▶│  wss://ws-subscriptions  │
            │                          │  real-time price stream  │
            │                          └──────────┬───────────────┘
            │ Opportunities                       │ WS-Signals (<1s)
            ▼                                     ▼
    ┌─────────────────────────────────────────────────┐
    │                 asyncio.Queue                    │
    └──────────────────────┬──────────────────────────┘
                           │
                           ▼
    ┌─────────────────────────────────────────────────┐
    │               SniperEngine                      │
    │  ┌──────────┐ ┌──────────┐ ┌────────────────┐   │
    │  │  CLOB    │ │   Risk   │ │   Position     │   │
    │  │ Verify   │ │ Manager  │ │   Monitor      │   │
    │  └──────────┘ └──────────┘ └────────────────┘   │
    └──────────────────────┬──────────────────────────┘
                           │
                           ▼
    ┌─────────────────────────────────────────────────┐
    │          Polymarket CLOB Client                  │
    │    market_buy · market_sell · auto-claim         │
    └─────────────────────────────────────────────────┘
```

**Flow:** Scanner discovers eligible markets on the Gamma API → WebSocket subscribes for sub-second price updates → when a token enters the buy window, the Engine verifies on the CLOB order book, checks risk limits, and fires a limit order → Position Monitor watches for resolution or quick-exit opportunity → Auto-Claimer redeems winning tokens back to USDC.e.

## Features

- **Dual-pipeline detection** — Gamma API polling for market discovery + Polymarket WebSocket for real-time price streaming (<1s latency)
- **All-market scanning** — Monitors 33K+ active markets per cycle, filters by volume, end date, and question-date extraction
- **Dynamic order sizing** — Distributes available balance evenly across open slots (`balance / available_slots`)
- **Quick-exit strategy** — Sells at market when bid reaches $0.995 instead of waiting for resolution, recycling capital instantly
- **Auto-claim** — Redeems resolved winning positions on-chain via Gnosis Safe `execTransaction`
- **Risk management** — Duplicate prevention, per-market limits, exposure cap, session loss circuit breaker, match cooldown
- **Stop-loss** — Automatic market sell when price drops below configurable threshold
- **Position monitor** — Checks official API resolution + price-based fallback + stale position cleanup
- **Circuit breaker** — Halts trading on scanner failures or stale data
- **State persistence** — Atomic JSON writes, survives Docker restarts
- **Telegram alerts** — Trade executions, resolutions, quick-exits, stop-losses, errors, circuit breaker events
- **Real-time dashboard** — Web UI at `http://localhost:8080` with positions, PnL history, WebSocket status, adapter health (auto-refresh 5s)
- **Rate limiting** — Configurable token bucket throttle for API calls
- **Docker ready** — Dockerfile + docker-compose with named volumes for data/logs and healthcheck
- **Dry-run mode** — Full pipeline without placing real orders
- **268 tests** — Unit, integration, and end-to-end with pytest

## Quick Start

### Prerequisites

- Python 3.12+
- A Polygon wallet with USDC.e + POL (for gas)
- Polymarket account (for proxy wallet address)

### 1. Clone & Install

```bash
git clone https://github.com/Kaelsz/Polymarket_Sniper.git
cd Polymarket_Sniper

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your credentials. The key variables:

```env
POLYMARKET_ADDRESS=0xYourProxyWalletAddress
POLY_PRIVATE_KEY=0xYourPrivateKey
POLY_SIGNATURE_TYPE=2
POLY_FUNDER=0xYourProxyWalletAddress
```

### 3. Derive API Keys

API credentials must be derived from your private key (not the ones from the Polymarket dashboard):

```bash
python derive_keys.py
```

Copy the output (`POLY_API_KEY`, `POLY_API_SECRET`, `POLY_API_PASSPHRASE`) into your `.env`.

### 4. Approve USDC.e & Set Allowances

```bash
python approve_usdc.py
python setup_allowance.py
```

### 5. Run

```bash
# Dry-run (default — simulates everything)
python main.py

# Live trading
DRY_RUN=false python main.py
```

### Docker (recommended for VPS)

```bash
docker compose up -d --build

# View logs
docker compose logs -f polysniper

# Stop
docker compose down
```

### Tests

```bash
pytest -v
```

## Configuration

All parameters via environment variables or `.env` file:

### Credentials

| Variable | Default | Description |
|---|---|---|
| `POLYMARKET_ADDRESS` | — | Polymarket proxy wallet address |
| `POLY_PRIVATE_KEY` | — | EOA private key (hex) |
| `POLY_API_KEY` | — | CLOB API key (from `derive_keys.py`) |
| `POLY_API_SECRET` | — | CLOB API secret |
| `POLY_API_PASSPHRASE` | — | CLOB API passphrase |
| `POLY_SIGNATURE_TYPE` | `0` | `0`=EOA, `1`=Poly Proxy, `2`=Gnosis Safe |
| `POLY_FUNDER` | — | Proxy wallet address (leave empty to use `POLYMARKET_ADDRESS`) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (optional) |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID (optional) |

### Trading

| Variable | Default | Description |
|---|---|---|
| `DRY_RUN` | `true` | Simulate trades without real orders |
| `MIN_BUY_PRICE` | `0.95` | Minimum ask price to buy |
| `MAX_BUY_PRICE` | `0.99` | Maximum ask price to buy |
| `ORDER_SIZE_USDC` | `50.0` | Base USDC per trade (used with sizing modes) |
| `SIZING_MODE` | `fixed` | Order sizing: `fixed`, `confidence`, or `kelly` |
| `MIN_ORDER_USDC` | `10.0` | Minimum order size |
| `MAX_ORDER_USDC` | `200.0` | Maximum order size |
| `EXIT_SELL_THRESHOLD` | `0.995` | Quick-exit: sell when bid reaches this price |

### Scanner

| Variable | Default | Description |
|---|---|---|
| `SCANNER_INTERVAL` | `30` | Seconds between Gamma API scans |
| `MIN_VOLUME_USDC` | `100000` | Minimum market volume ($) to consider |
| `MAX_END_HOURS` | `24` | Only markets ending within this window |

### Risk Management

| Variable | Default | Description |
|---|---|---|
| `MAX_OPEN_POSITIONS` | `10` | Maximum concurrent positions |
| `MAX_POSITIONS_PER_GAME` | `10` | Max positions per market |
| `MAX_SESSION_LOSS_USDC` | `200.0` | Halt trading after this loss |
| `MAX_TOTAL_EXPOSURE_USDC` | `500.0` | Maximum total capital at risk |
| `MATCH_COOLDOWN_SECONDS` | `30.0` | Cooldown between trades on same market |
| `FEE_RATE` | `0.02` | Fee rate on winning positions |
| `STOP_LOSS_PCT` | `0.0` | Stop-loss threshold (0=disabled, 0.3=30%) |

### Infrastructure

| Variable | Default | Description |
|---|---|---|
| `API_RATE_LIMIT` | `5.0` | API calls per second |
| `API_RATE_BURST` | `10` | Burst capacity for rate limiter |
| `DASHBOARD_PORT` | `8080` | HTTP dashboard port |
| `LOG_DIR` | `logs` | Log file directory |
| `LOG_MAX_BYTES` | `10485760` | Max log file size (10 MB) |
| `LOG_BACKUP_COUNT` | `5` | Number of rotated log backups |

## Project Structure

```
Polymarket_Sniper/
├── core/
│   ├── config.py            # Settings from environment variables
│   ├── scanner.py           # MarketScanner — Gamma API polling + pre-filter
│   ├── ws_stream.py         # PriceStream — real-time WebSocket price feed
│   ├── engine.py            # SniperEngine — CLOB verify + trade execution + monitor
│   ├── polymarket.py        # Async Polymarket CLOB client wrapper
│   ├── risk.py              # RiskManager — limits, dedup, PnL tracking, fees
│   ├── sizing.py            # OrderSizer — fixed / confidence / kelly sizing
│   ├── claimer.py           # PositionClaimer — on-chain auto-redeem via Safe
│   ├── circuit_breaker.py   # Scanner health monitoring
│   ├── rate_limiter.py      # Token bucket rate limiter
│   ├── persistence.py       # JSON state persistence (atomic writes)
│   └── dashboard.py         # Real-time HTTP dashboard (aiohttp)
├── utils/
│   └── alerts.py            # Telegram notification system
├── backtest/
│   ├── scenario.py          # Backtesting scenario loader
│   ├── runner.py            # Backtest execution engine
│   └── report.py            # Performance report generator
├── tests/                   # 268 tests (pytest + pytest-asyncio)
├── scenarios/               # Example backtest scenarios
├── main.py                  # Entry point + async orchestrator
├── derive_keys.py           # Generate CLOB API credentials from private key
├── approve_usdc.py          # Approve USDC.e for Polymarket contracts
├── setup_allowance.py       # Set CLOB balance allowances
├── debug_api.py             # API credential diagnostic tool
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## VPS Deployment Guide

### 1. Server Setup (Ubuntu 22.04+)

```bash
apt update && apt install -y docker.io docker-compose git python3 python3-pip python3-venv
```

### 2. Clone & Configure

```bash
git clone https://github.com/Kaelsz/Polymarket_Sniper.git
cd Polymarket_Sniper

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 derive_keys.py
# Copy output to .env

cp .env.example .env
nano .env
# Fill in credentials + set DRY_RUN=false

python3 approve_usdc.py
python3 setup_allowance.py
python3 debug_api.py   # Verify auth works
```

### 3. Launch

```bash
docker compose up -d --build
docker compose logs -f polysniper
```

### 4. Update

```bash
git pull
docker compose up -d --build
```

## Disclaimer

This software is for educational purposes. Trading on prediction markets involves financial risk. Use at your own risk and always start with dry-run mode.
