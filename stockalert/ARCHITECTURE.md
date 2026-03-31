# Stock Alert Tool — Architecture & Build Plan

> A real-time stock monitoring and alert system powered by ClickHouse, FastAPI, and Claude AI.
> Streams 1-minute OHLCV data from Polygon.io, Alpaca, and ThinkOrSwim into a high-performance
> analytics database with a Python backend, WebSocket push, and LLM-assisted signal analysis.

---

## Table of Contents

1. [Tech Stack](#tech-stack)
2. [System Architecture](#system-architecture)
3. [Project Structure](#project-structure)
4. [Database Design](#database-design)
5. [Data Ingestion Layer](#data-ingestion-layer)
6. [Backend API](#backend-api)
7. [Indicator & Alert Engine](#indicator--alert-engine)
8. [LLM Integration](#llm-integration)
9. [Frontend](#frontend)
10. [Build Phases](#build-phases)
11. [Environment & Config](#environment--config)
12. [Key Conventions](#key-conventions)

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Database | ClickHouse (Docker) | MergeTree engine, columnar, time-series optimized |
| CH Driver | `clickhouse-connect` | Official Python client, async-friendly |
| Ingestion | `asyncio` + `websockets` | Concurrent feed handlers |
| Backend | FastAPI | Async REST + WebSocket endpoints |
| Indicators | `pandas-ta` | All standard technicals pre-built |
| Scheduling | `APScheduler` | Periodic indicator calculation jobs |
| LLM | `anthropic` SDK | Claude API for signal analysis |
| Frontend | React + Vite | TradingView Lightweight Charts |
| Charts | TradingView Lightweight Charts | Free, professional-grade candlesticks |
| Config | `pydantic-settings` + `.env` | Type-safe environment management |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      DATA SOURCES                           │
│   Polygon.io WS    │    Alpaca WS    │   ThinkOrSwim WS    │
└──────────┬─────────┴────────┬────────┴──────────┬──────────┘
           │                  │                   │
           └──────────────────▼───────────────────┘
                    ┌─────────────────────┐
                    │  Python Ingestion   │
                    │  (asyncio streams)  │
                    │  Normalizes all 3   │
                    │  feeds → 1 schema   │
                    └──────────┬──────────┘
                               │  batch insert
                               ▼
              ┌────────────────────────────────┐
              │           ClickHouse           │
              │                                │
              │  ohlcv_1m          indicators  │
              │  ohlcv_historical  alerts      │
              │  alert_rules       symbols     │
              │                                │
              │  Materialized views auto-      │
              │  compute aggregations          │
              └──────────────┬─────────────────┘
                             │
                    ┌────────▼────────┐
                    │  FastAPI        │
                    │  ├ REST API     │
                    │  ├ WS push      │
                    │  ├ Alert engine │
                    │  └ LLM layer    │
                    └────────┬────────┘
                             │
              ┌──────────────▼──────────────┐
              │                             │
       ┌──────▼──────┐            ┌─────────▼──────┐
       │   React     │            │   Claude API   │
       │  Dashboard  │            │  Signal Intel  │
       └─────────────┘            └────────────────┘
```

---

## Project Structure

```
stock-alert/
├── docker-compose.yml          # ClickHouse + app services
├── .env                        # secrets and config (never commit)
├── .env.example                # template for .env
├── requirements.txt
│
├── db/
│   ├── client.py               # clickhouse-connect singleton
│   ├── init.py                 # CREATE TABLE IF NOT EXISTS (runs on startup)
│   └── queries.py              # reusable typed query functions
│
├── ingestion/
│   ├── stream_manager.py       # runs all feed coroutines concurrently
│   ├── base_adapter.py         # abstract base class for all adapters
│   └── adapters/
│       ├── polygon.py          # Polygon.io WebSocket → normalized row
│       ├── alpaca.py           # Alpaca WebSocket → normalized row
│       └── thinkorswim.py      # TOS WebSocket → normalized row
│
├── indicators/
│   ├── calculator.py           # pandas-ta calculations per symbol
│   └── scheduler.py           # APScheduler job: runs after each candle close
│
├── alerts/
│   ├── engine.py               # evaluates alert_rules against latest indicators
│   └── rules.py                # built-in rule types (RSI, MACD cross, etc.)
│
├── api/
│   ├── main.py                 # FastAPI app factory, startup/shutdown hooks
│   ├── dependencies.py         # shared DB client, auth
│   ├── routes/
│   │   ├── candles.py          # GET /candles/{symbol}
│   │   ├── indicators.py       # GET /indicators/{symbol}
│   │   ├── alerts.py           # GET/POST/DELETE /alerts
│   │   ├── symbols.py          # GET /symbols (watchlist)
│   │   └── backtest.py         # POST /backtest
│   └── ws/
│       └── broadcaster.py      # WebSocket manager, pushes live data to UI
│
├── llm/
│   ├── analyst.py              # builds context from CH data → Claude API
│   └── prompts.py              # prompt templates
│
└── frontend/
    ├── src/
    │   ├── components/
    │   │   ├── CandleChart.tsx  # TradingView Lightweight Charts
    │   │   ├── AlertFeed.tsx    # live alert ticker
    │   │   ├── Watchlist.tsx    # symbol list with mini stats
    │   │   └── AIAnalyst.tsx    # chat interface to Claude
    │   ├── hooks/
    │   │   └── useWebSocket.ts  # WS connection to FastAPI
    │   └── App.tsx
    └── package.json
```

---

## Database Design

### Design Principles

- **`MergeTree` engine** on all tables — optimized for time-series inserts and range scans
- **`PARTITION BY toYYYYMM(timestamp)`** — keeps partitions manageable, fast for date-range queries
- **`ORDER BY (symbol, timestamp)`** — primary sort key; all queries filter on symbol first
- **`LowCardinality(String)`** for symbol and source — dictionary-encoded, ~10x compression
- **`IF NOT EXISTS`** on all DDL — safe to re-run `init.py` on every startup

### Tables

#### `ohlcv_1m` — Core 1-minute candles

```sql
CREATE TABLE IF NOT EXISTS ohlcv_1m (
    symbol        LowCardinality(String),
    timestamp     DateTime64(3, 'UTC'),
    open          Float64,
    high          Float64,
    low           Float64,
    close         Float64,
    volume        Float64,
    vwap          Float64,
    trade_count   UInt32,
    source        LowCardinality(String)   -- 'polygon' | 'alpaca' | 'tos'
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (symbol, timestamp)
SETTINGS index_granularity = 8192;
```

#### `ohlcv_historical` — Bulk historical data for backtesting

```sql
CREATE TABLE IF NOT EXISTS ohlcv_historical (
    symbol        LowCardinality(String),
    timestamp     DateTime64(3, 'UTC'),
    timeframe     LowCardinality(String),  -- '1m' | '5m' | '1d'
    open          Float64,
    high          Float64,
    low           Float64,
    close         Float64,
    volume        Float64,
    vwap          Float64,
    source        LowCardinality(String)
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (symbol, timeframe, timestamp);
```

#### `indicators` — Pre-computed technical indicators

```sql
CREATE TABLE IF NOT EXISTS indicators (
    symbol        LowCardinality(String),
    timestamp     DateTime64(3, 'UTC'),
    rsi_14        Nullable(Float64),
    ema_9         Nullable(Float64),
    ema_21        Nullable(Float64),
    ema_50        Nullable(Float64),
    macd          Nullable(Float64),
    macd_signal   Nullable(Float64),
    macd_hist     Nullable(Float64),
    bb_upper      Nullable(Float64),
    bb_mid        Nullable(Float64),
    bb_lower      Nullable(Float64),
    vwap_session  Nullable(Float64),
    atr_14        Nullable(Float64),
    volume_sma_20 Nullable(Float64)
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (symbol, timestamp);
```

#### `alert_rules` — User-defined trigger conditions

```sql
CREATE TABLE IF NOT EXISTS alert_rules (
    id            UUID DEFAULT generateUUIDv4(),
    symbol        LowCardinality(String),
    rule_type     LowCardinality(String),  -- 'RSI_OVERSOLD' | 'MACD_CROSS' | 'PRICE_ABOVE' | etc.
    params        String,                  -- JSON: {"threshold": 30}
    enabled       Bool DEFAULT true,
    created_at    DateTime64(3, 'UTC') DEFAULT now64()
)
ENGINE = MergeTree()
ORDER BY (symbol, rule_type);
```

#### `alerts` — Fired alert log

```sql
CREATE TABLE IF NOT EXISTS alerts (
    id            UUID DEFAULT generateUUIDv4(),
    symbol        LowCardinality(String),
    timestamp     DateTime64(3, 'UTC'),
    rule_type     LowCardinality(String),
    message       String,
    price         Float64,
    severity      Enum8('low'=1, 'medium'=2, 'high'=3),
    acknowledged  Bool DEFAULT false
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (symbol, timestamp);
```

#### `symbols` — Watchlist

```sql
CREATE TABLE IF NOT EXISTS symbols (
    symbol        String,
    name          String,
    active        Bool DEFAULT true,
    added_at      DateTime64(3, 'UTC') DEFAULT now64()
)
ENGINE = MergeTree()
ORDER BY symbol;
```

---

## Data Ingestion Layer

### Design Pattern

Each data source gets its own adapter class that inherits from `BaseAdapter`. The adapter is
responsible for:

1. Connecting to the WebSocket
2. Subscribing to the configured symbols
3. Parsing each message into a **normalized `OHLCVRow`** dataclass
4. Handing off rows to a shared async queue

The `StreamManager` runs all adapters concurrently with `asyncio.gather` and drains the
shared queue in batches, inserting into ClickHouse every N rows or every T seconds
(whichever comes first). Batching is critical — never insert row-by-row.

### Normalized Schema (Python dataclass)

```python
@dataclass
class OHLCVRow:
    symbol: str
    timestamp: datetime          # UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float
    trade_count: int
    source: str                  # 'polygon' | 'alpaca' | 'tos'
```

### Batch Insert Strategy

```python
# Insert when batch reaches 500 rows OR every 5 seconds
BATCH_SIZE = 500
FLUSH_INTERVAL_SECONDS = 5
```

---

## Backend API

### REST Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/candles/{symbol}` | OHLCV for a symbol, date range, timeframe |
| `GET` | `/indicators/{symbol}` | Latest indicator values |
| `GET` | `/indicators/{symbol}/history` | Indicator history for charting |
| `GET` | `/alerts` | All fired alerts, filterable |
| `POST` | `/alerts/rules` | Create a new alert rule |
| `DELETE` | `/alerts/rules/{id}` | Delete an alert rule |
| `PATCH` | `/alerts/{id}/acknowledge` | Mark alert as seen |
| `GET` | `/symbols` | Active watchlist |
| `POST` | `/symbols` | Add symbol to watchlist |
| `POST` | `/backtest` | Run a backtest over historical data |
| `POST` | `/llm/analyze` | Get Claude analysis for a symbol |
| `GET` | `/health` | Health check |

### WebSocket

- **`WS /ws/live`** — Pushes real-time updates to the frontend
  - Emits: `candle_update`, `alert_fired`, `indicator_update`
  - Format: `{ "type": "candle_update", "symbol": "AAPL", "data": {...} }`

---

## Indicator & Alert Engine

### Indicator Calculation Flow

```
After each 1-min candle closes for a symbol:
  1. Fetch last N candles from ohlcv_1m (enough for longest lookback, e.g. 200)
  2. Build a pandas DataFrame
  3. Run pandas-ta to compute all indicators
  4. Insert latest indicator row into `indicators` table
  5. Run alert engine against new indicator values
```

### Built-in Alert Rule Types

| Rule Type | Trigger Condition |
|---|---|
| `RSI_OVERSOLD` | RSI(14) crosses below threshold (default 30) |
| `RSI_OVERBOUGHT` | RSI(14) crosses above threshold (default 70) |
| `MACD_BULLISH_CROSS` | MACD line crosses above signal line |
| `MACD_BEARISH_CROSS` | MACD line crosses below signal line |
| `PRICE_ABOVE` | Close price crosses above a level |
| `PRICE_BELOW` | Close price crosses below a level |
| `VOLUME_SPIKE` | Volume > N × 20-period average volume |
| `BB_SQUEEZE` | Bollinger Band width below threshold |
| `EMA_CROSS_BULL` | EMA9 crosses above EMA21 |
| `EMA_CROSS_BEAR` | EMA9 crosses below EMA21 |

---

## LLM Integration

### Strategy

The LLM layer pulls a compact context snapshot from ClickHouse and sends it to Claude.
Claude is **not** given raw tick data — it receives a structured summary: recent candles,
current indicator values, any fired alerts, and a prompt asking for analysis.

### Context Builder Output (fed to Claude)

```
Symbol: AAPL  |  As of: 2025-03-31 14:32 UTC

Recent 1-min candles (last 10):
  [table of OHLCV]

Current indicators:
  RSI(14): 28.4 — OVERSOLD
  MACD: -0.42 | Signal: -0.18 | Hist: -0.24
  EMA9: 171.22 | EMA21: 172.08 — bearish alignment
  BB: 168.40 / 172.10 / 175.80 — price near lower band

Recent alerts fired (last 30 min):
  - RSI_OVERSOLD triggered at 14:18
  - MACD_BEARISH_CROSS triggered at 14:05

Question: What does this setup suggest? Any actionable observations?
```

### Use Cases

- **Signal explanation** — "Why did this alert fire? What does it mean?"
- **Market summary** — "Summarize conditions across all my symbols right now"
- **Backtest interpretation** — "What does this backtest result tell me?"
- **Chat interface** — Free-form Q&A about any symbol in the watchlist

> ⚠️ Claude provides analysis and context, not financial advice. Always apply your own
> judgment before making any trading decisions.

---

## Frontend

### Pages / Views

| View | Description |
|---|---|
| Dashboard | Watchlist overview, mini charts, active alerts |
| Symbol Detail | Full candlestick chart, indicator overlays, alert history |
| Alert Manager | Create/edit/delete alert rules, view alert log |
| Backtest | Run and visualize backtests on historical data |
| AI Analyst | Chat with Claude about your symbols and signals |

### Real-time Updates

The frontend connects to `WS /ws/live` on load. Incoming messages update:
- The active candle on any open chart (no page refresh needed)
- The alert feed ticker
- Symbol stats in the watchlist

### Charting

Use **TradingView Lightweight Charts** (free, open-source):
- Candlestick series for OHLCV
- Line series overlays for EMA9, EMA21, EMA50
- Area series for VWAP
- Histogram series for MACD
- Separate pane for RSI with overbought/oversold bands

---

## Build Phases

### Phase 1 — Data Foundation *(start here)*

- [ ] ClickHouse running in Docker with persistent volumes
- [ ] `db/client.py` — clickhouse-connect singleton
- [ ] `db/init.py` — all `CREATE TABLE IF NOT EXISTS` statements
- [ ] `db/init.py` runs automatically on FastAPI startup
- [ ] `ingestion/adapters/polygon.py` — connect, subscribe, normalize
- [ ] `ingestion/adapters/alpaca.py` — connect, subscribe, normalize
- [ ] `ingestion/adapters/thinkorswim.py` — connect, subscribe, normalize
- [ ] `ingestion/stream_manager.py` — runs all 3 concurrently, batch inserts
- [ ] Verify data flowing into `ohlcv_1m` — spot-check with `clickhouse-client`

### Phase 2 — Intelligence Layer

- [ ] `indicators/calculator.py` — pandas-ta integration
- [ ] `indicators/scheduler.py` — APScheduler job triggers after each candle
- [ ] `alerts/engine.py` — evaluates rules, writes to `alerts` table
- [ ] WebSocket broadcaster pushes `alert_fired` events

### Phase 3 — Backend API

- [ ] FastAPI app with all REST routes
- [ ] WebSocket endpoint with connection manager
- [ ] `/backtest` endpoint querying `ohlcv_historical`
- [ ] Auth (API key header, simple for now)

### Phase 4 — Frontend

- [ ] React + Vite scaffold
- [ ] TradingView chart component
- [ ] WebSocket hook for live data
- [ ] Watchlist and alert feed
- [ ] Symbol detail page with indicator overlays

### Phase 5 — LLM Integration

- [ ] `llm/analyst.py` — context builder + Claude API call
- [ ] `/llm/analyze` REST endpoint
- [ ] AI Analyst chat view in frontend

### Phase 6 — Polish & Reliability

- [ ] Reconnect logic on WebSocket disconnects (all 3 feeds)
- [ ] Logging with `structlog`
- [ ] Basic monitoring / health checks
- [ ] Docker Compose for full local stack

---

## Environment & Config

### `.env` structure

```env
# ClickHouse
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=yourpassword
CLICKHOUSE_DATABASE=stocks

# Data Sources
POLYGON_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
TOS_USERNAME=
TOS_PASSWORD=

# LLM
ANTHROPIC_API_KEY=

# App
WATCHLIST=AAPL,MSFT,NVDA,TSLA,SPY,QQQ,AMD,META,GOOGL,AMZN
ENVIRONMENT=development
```

### Pydantic Settings

```python
# config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "default"
    clickhouse_password: str
    clickhouse_database: str = "stocks"

    polygon_api_key: str = ""
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""

    anthropic_api_key: str

    watchlist: list[str] = []

    class Config:
        env_file = ".env"
```

---

## Key Conventions

- **Always batch inserts** — never insert a single row at a time into ClickHouse
- **UTC everywhere** — all timestamps stored and handled in UTC, convert to local only in the UI
- **Normalize at the edge** — adapters own the normalization; nothing downstream knows which feed a row came from
- **`IF NOT EXISTS` on all DDL** — `db/init.py` is idempotent and safe to run on every startup
- **`Nullable(Float64)` for indicators** — early candles won't have enough history for all indicators; nulls beat fake zeros
- **`LowCardinality(String)` for symbol and source** — ClickHouse dictionary-encodes these automatically, significant compression and query speed gains for repeated values
- **Alert engine is read-only from CH** — it reads indicators, writes alerts; it never modifies raw candle data
- **Claude gets summaries, not raw data** — context builder shapes the prompt; never dump a full DataFrame into an LLM call
- **One adapter per feed** — keep feed-specific quirks (reconnect logic, message parsing, auth) fully isolated in their adapter file
