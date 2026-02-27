# PolySniper v1.0

Esport latency arbitrage bot for [Polymarket](https://polymarket.com). Detects match results from live esport feeds before markets adjust, and places automated trades on outcome tokens.

## Architecture

```
                   Live Data Sources
           ┌──────────┬──────────┬──────────┐
           │  CS2     │  LoL     │ Valorant │  Dota2
           │ (WS/SSE)│ (REST)   │ (REST)   │ (REST)
           └────┬─────┴────┬─────┴────┬─────┴────┬──┘
                │          │          │          │
                ▼          ▼          ▼          ▼
           ┌─────────────────────────────────────────┐
           │           asyncio.Queue                  │
           └──────────────────┬──────────────────────┘
                              │ MatchEvent
                              ▼
           ┌─────────────────────────────────────────┐
           │           SniperEngine                   │
           │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
           │  │  Fuzzy   │ │   Risk   │ │ Position │ │
           │  │  Mapper  │ │ Manager  │ │ Monitor  │ │
           │  └──────────┘ └──────────┘ └──────────┘ │
           └──────────────────┬──────────────────────┘
                              │
                              ▼
           ┌─────────────────────────────────────────┐
           │        Polymarket CLOB Client            │
           │   market_buy · market_sell · best_ask    │
           └─────────────────────────────────────────┘
```

## Features

- **4 esport adapters** — CS2 (WebSocket/SSE), League of Legends, Valorant, Dota 2 (REST polling) with automatic reconnection and exponential backoff
- **Fuzzy team mapping** — Maps raw team names to Polymarket tokens using `rapidfuzz`, with alias expansion (NAVI, G2, SEN, etc.)
- **Risk management** — Duplicate trade prevention, per-match cooldown, position limits (global + per-game), exposure cap, session loss circuit breaker
- **Polymarket fees** — 2% fee deduction on winning positions (configurable)
- **Stop-loss** — Automatic market sell when price drops below threshold
- **Position monitor** — Background loop checks official API resolution + price-based fallback
- **Circuit breaker** — Monitors adapter health, halts trading on too many failures or stale data
- **State persistence** — JSON atomic writes, survives restarts
- **Telegram alerts** — Trade executions, stop-loss triggers, crashes, circuit breaker events
- **Real-time HTTP dashboard** — Live web UI at `http://localhost:8080` showing positions, PnL, adapter health, trades (auto-refreshes every 5s)
- **Rotating file logs** — 10 MB per file, 5 backups (configurable)
- **Docker ready** — Dockerfile + docker-compose with named volumes for data and logs
- **Dry-run mode** — Full pipeline without placing real orders
- **289 tests** — Unit, integration, and end-to-end with pytest

## Quick Start

### Prerequisites

- Python 3.12+
- A Polygon wallet with USDC (for live trading)
- Polymarket CLOB API access

### Setup

```bash
git clone https://github.com/Kaelsz/Polymarket_Sniper.git
cd Polymarket_Sniper

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env with your credentials
```

### Run

```bash
# Dry-run mode (default)
python main.py

# Live trading
DRY_RUN=false python main.py
```

### Docker

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

All parameters are set via environment variables (or `.env` file):

| Variable | Default | Description |
|---|---|---|
| `POLYMARKET_ADDRESS` | — | Polygon wallet address |
| `POLY_PRIVATE_KEY` | — | Wallet private key |
| `POLYMARKET_HOST` | `https://clob.polymarket.com` | CLOB API endpoint |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (optional) |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID (optional) |
| `DRY_RUN` | `true` | Simulate trades without placing orders |
| `MAX_BUY_PRICE` | `0.85` | Max ask price to accept (1.0 = certain) |
| `ORDER_SIZE_USDC` | `50.0` | USDC amount per trade |
| `MAX_OPEN_POSITIONS` | `10` | Maximum concurrent open positions |
| `MAX_POSITIONS_PER_GAME` | `4` | Max positions per game (CS2, LoL, etc.) |
| `MAX_SESSION_LOSS_USDC` | `200.0` | Halt trading after this session loss |
| `MAX_TOTAL_EXPOSURE_USDC` | `500.0` | Maximum total capital at risk |
| `MATCH_COOLDOWN_SECONDS` | `30.0` | Cooldown between trades on same match |
| `FEE_RATE` | `0.02` | Polymarket fee rate on winning positions |
| `STOP_LOSS_PCT` | `0.0` | Stop-loss threshold (0 = disabled, 0.3 = 30%) |
| `DASHBOARD_PORT` | `8080` | HTTP dashboard port |
| `CB_FAILURE_THRESHOLD` | `3` | Adapter failures before circuit opens |
| `CB_MIN_HEALTHY_ADAPTERS` | `1` | Min healthy adapters to keep trading |
| `CB_STALE_DATA_TIMEOUT` | `120.0` | Seconds before adapter data is stale |
| `LOG_DIR` | `logs` | Log file directory |
| `LOG_MAX_BYTES` | `10485760` | Max log file size (10 MB) |
| `LOG_BACKUP_COUNT` | `5` | Number of rotated log files to keep |

## Project Structure

```
polymarket_trade/
├── adapters/
│   ├── base.py              # BaseAdapter ABC + MatchEvent dataclass
│   ├── cs2_adapter.py       # Counter-Strike 2 (WebSocket + SSE)
│   ├── lol_adapter.py       # League of Legends (REST polling)
│   ├── valorant_adapter.py  # Valorant (REST polling)
│   └── dota2_adapter.py     # Dota 2 (REST polling)
├── core/
│   ├── config.py            # Settings from environment variables
│   ├── engine.py            # SniperEngine — event consumer + trade executor
│   ├── mapper.py            # FuzzyMapper — team name → token ID
│   ├── polymarket.py        # Async Polymarket CLOB client wrapper
│   ├── risk.py              # RiskManager — limits, dedup, PnL, fees
│   ├── circuit_breaker.py   # Adapter health monitoring
│   ├── persistence.py       # JSON state persistence (atomic writes)
│   └── dashboard.py         # Real-time HTTP dashboard (aiohttp)
├── utils/
│   └── alerts.py            # Telegram notification system
├── tests/                   # 271 tests (pytest + pytest-asyncio)
├── main.py                  # Entry point + orchestrator
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## How It Works

1. **Adapters** connect to live esport data sources and emit `MatchEvent` objects (team won, game, match ID) into an async queue
2. **SniperEngine** consumes events and uses the **FuzzyMapper** to find the matching Polymarket "Will X win?" market
3. **RiskManager** validates the trade (dedup, cooldown, limits, exposure) before execution
4. **PolymarketClient** places a market-buy order on the YES token at the current ask price
5. **Position Monitor** runs in background, checking for official market resolution or price-based heuristics to close positions and record realized PnL
6. **Circuit Breaker** monitors adapter health and halts all trading if too many feeds go down

## Disclaimer

This software is for educational purposes. Trading on prediction markets involves financial risk. Use at your own risk and always start with dry-run mode.
