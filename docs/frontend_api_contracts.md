# Frontend API Contracts — Plan & Audit

How the React cockpit (`frontend/`) talks to the FastAPI backend
(`app/`). This document is the **contract surface**: every endpoint
the frontend depends on, its current typing posture, gaps, and the
microservice rules every new endpoint must satisfy.

**Status:** **APPROVED 2026-05-18.** All seven §10 questions locked
(see §10 for the decisions). FE-CONTRACTS-1 starts next.

**Companion docs:**

- [frontend_plan.md](frontend_plan.md) — UI architecture, page catalog,
  SaaS-readiness seams.
- [streaming_universe_model.md](streaming_universe_model.md) — what gets
  streamed to ClickHouse (read §10.1 before touching watchlists).
- [ARCHITECTURE.md](ARCHITECTURE.md) — service map.

---

## 1. Why this doc exists

The cockpit's first two pages (`/` Status, `/symbol/:ticker`) shipped
in FE-1 and FE-2 with the frontend type-system mostly making
endpoint-shape assertions through hand-rolled interfaces. That works
for two pages. It does **not** work for thirteen.

The plan in [frontend_plan.md §8](frontend_plan.md) commits to a
closed type-chain:

```
Pydantic schema  →  /openapi.json  →  TypeScript types  →  React components
```

For that chain to be load-bearing, **every endpoint the cockpit calls
must declare a Pydantic response_model**. Today, less than half do.
This document inventories the surface, names the missing models, and
defines the rules so new routes land right the first time.

---

## 2. The audit (current state, 2026-05-18)

### 2.1 Endpoints WITH `response_model` — 10 routes

These are already correctly typed. The cockpit picks up their shapes
via `npm run codegen` with zero hand-rolled interface code on the
frontend side.

| Route | Response model | Purpose |
|---|---|---|
| `POST /api/screener/scan` | `ScreenerResult` | Run a screener spec |
| `GET  /api/indicators/series` | `IndicatorSeries` | Single indicator over a window |
| `POST /api/indicators/chart-data` | `IndicatorChartData` | Multi-indicator overlay for a chart |
| `GET  /api/lake/bars` | `BronzeBarsResponse` | Iceberg bronze read |
| `GET  /api/lake/symbols` | `LakeSymbolsResponse` | Bronze symbol catalog |
| `GET  /api/lake/last-day` | `LakeLatestDayResponse` | Latest-day bronze sample |
| `GET  /api/health/services` | `HealthServicesResponse` | Composite cockpit health (FE-1.5) |
| `GET  /api/corp-actions/{symbol}` | `CorpActionsResponse` | Splits/divs from silver |
| `GET  /api/silver/bars` | `SilverBarsResponse` | Silver-tier OHLCV |
| `GET  /api/silver/quality` | `BarQualityResponse` | Per-bar quality flags |

### 2.2 Endpoints WITHOUT `response_model` — gaps to close

These return `dict` or `list[dict]`. OpenAPI emits `unknown` for the
response. The frontend either hand-rolls types (FE-2 `useSymbolBars`)
or has no types at all (FE-3+ would have to repeat the pattern).

| Route | Returns | Used by (FE phase) | Severity |
|---|---|---|---|
| `GET  /api/signals` | `list[dict]` | FE-2 Symbol page | **High** |
| `GET  /api/bars` | `list[dict]` | FE-2 Symbol page | **High** |
| `GET  /api/journal/accounts` | `dict` | FE-8 Journal | High |
| `GET  /api/journal/trades` | `dict` | FE-8 Journal | High |
| `GET  /api/journal/summary` | `dict` | FE-8 Journal | High |
| `PUT  /api/journal/notes/{id}` | `dict` | FE-8 Journal | Medium |
| `POST /api/journal/sync` | `dict` | FE-8 Journal | Medium |
| `GET  /watchlist` (legacy single) | `dict` | FE legacy / deprecate | Low (delete) |
| `POST /watchlist/add` | `dict` | FE legacy / deprecate | Low (delete) |
| `POST /watchlist/remove` | `dict` | FE legacy / deprecate | Low (delete) |
| `GET  /watchlist/snapshot` | `dict` | FE legacy / deprecate | Low (delete) |
| `GET  /api/watchlists` | `list[dict]` | FE-3 Watchlists | High |
| `POST /api/watchlists` | `dict` | FE-3 Watchlists | High |
| `GET  /api/watchlists/{name}` | `dict` | FE-3 Watchlists | High |
| `PATCH /api/watchlists/{name}` | `dict` | FE-3 Watchlists | High |
| `DELETE /api/watchlists/{name}` | `dict` | FE-3 Watchlists | High |
| `GET  /api/watchlists/{name}/members` | `dict` | FE-3 Watchlists | High |
| `POST /api/watchlists/{name}/members` | `dict` | FE-3 Watchlists | High |
| `DELETE /api/watchlists/{name}/members` | `dict` | FE-3 Watchlists | High |
| `GET  /api/watchlists/{name}/snapshot` | `dict` | FE-3 Watchlists | High |
| `POST /api/backfill[/deep,/daily,/intraday,/gaps]` (5 routes) | `dict` | FE-7 Coverage | High |
| `GET  /api/backfill/gaps` | `dict` | FE-7 Coverage | High |
| `GET  /api/backfill/coverage` | `dict` | FE-7 Coverage | High |
| `GET  /api/backfill/status` | `dict` | FE-1 StatusBar (already) | **High** |
| `GET  /api/instruments/search` | `dict` | FE-2 Symbol picker | **High** |
| `GET  /api/market/banner` | `dict` | FE Market banner | **High** |
| `GET  /api/movers` | `dict` | FE Daily movers | **High** |
| `GET  /monitors` | `dict` | FE Monitors | Medium |
| `POST /monitors/start` | `dict` | FE Monitors | Medium |
| `POST /monitors/stop` | `dict` | FE Monitors | Medium |
| `POST /api/backtest` | accepts `dict`, returns `dict` | FE-4 Backtest | **Critical — zero typing both directions** |

**Count:** 30 routes need response models. Plus `POST /api/backtest`
needs a *request* model too.

### 2.3 Endpoints that are MISSING entirely

The cockpit pages in the user spec need these endpoints; none exist
today.

| Capability | Missing endpoint(s) | Storage gap? |
|---|---|---|
| Manage seed universe from FE | `GET /api/v1/seed`, `POST /api/v1/seed`, `DELETE /api/v1/seed/{symbol}` | Yes — today `SEED_SYMBOLS` is env-only |
| Promote ad-hoc → seed (referenced in [frontend_plan.md §5.2](frontend_plan.md)) | `POST /api/v1/seed/promote` | Same gap as above |
| Change streaming provider from FE | `GET /api/v1/config/streaming`, `PUT /api/v1/config/streaming` | Yes — today `DATA_PROVIDER` is env-only; provider switch requires process restart |
| Simulated paper trades | `GET /api/v1/sim/trades`, `POST /api/v1/sim/trades`, `DELETE /api/v1/sim/trades/{id}`, `GET /api/v1/sim/positions`, `GET /api/v1/sim/equity-curve` | Yes — no `sim_trades` CH table |
| Ad-hoc ClickHouse query | `POST /api/v1/clickhouse/query`, `GET /api/v1/clickhouse/schema` | No (CH client exists) — but **read-only guard required** |
| List strategy runs (currently MCP-only) | `GET /api/v1/runs`, `GET /api/v1/runs/{run_id}` | No (`agent_runs` table exists) |
| Reproducibility replay | `POST /api/v1/runs/{run_id}/replay` | No |
| Topic-multiplexed WebSocket | `WS /ws/events` (replacing `/ws/signals`) | Pub/sub adapter needed |

### 2.4 Prefix / mount inconsistencies (technical debt)

Discovered during audit. Cleaning these up is a precondition for the
type chain to stay sane.

1. **Watchlists module mixes prefixes.** [`routes_watchlist.py`](../app/api/routes_watchlist.py)
   hardcodes `/api/watchlists` *inside the router*, then is mounted at
   `prefix=""` in `main_api.py`. That means a future change to the
   mount prefix would double-prefix. Fix: strip the `/api/` from the
   route paths and let `main_api.py` apply the prefix.
2. **Legacy single-watchlist routes live at `/watchlist`, not `/api/watchlist`.**
   Same module, same issue. The legacy single-watchlist surface is
   slated for deletion; the inconsistency is harmless if we delete it
   soon. Otherwise: rename.
3. **`/monitors` lives at root**, not `/api/monitors`. Mounted with
   `prefix=""`. Fix in the same pass as watchlists.

---

## 3. Microservice contract rules

These rules are what makes the cockpit ↔ backend boundary survive
multi-page growth, MCP-agent traffic, future SaaS, and eventual
backend-team-of-multiple-people.

### 3.1 Every route MUST declare `response_model`

No exceptions. Even routes that return a one-key wrapper like
`{"ok": true}` get a `class OkResponse(BaseModel)`. The
machine-readable contract is what enables type-safe frontend
generation, MCP tool schemas, automated mock servers, and the
upcoming /api/v1 namespace.

Routes that violate this rule do not get merged.

### 3.2 Every error MUST use the typed error envelope

```python
class ErrorResponse(BaseModel):
    code: str          # 'validation_error', 'not_found', 'conflict', 'rate_limited', ...
    message: str       # operator-readable; safe to surface in UI
    details: dict | None = None   # field-level errors, retry-after, etc.
    request_id: str | None = None # for log correlation
```

FastAPI's default `{"detail": "..."}` shape is **deprecated** for new
routes. We move to the envelope via a custom exception handler.

The frontend's `apiClient` middleware translates non-2xx responses
into a `Result<T, ErrorResponse>` type the components consume; no
component sees a `try/catch` around `fetch`.

### 3.3 Mutations MUST be idempotent OR return 409

The cockpit will (post-FE-10) optimistically retry on network blip.
Every `POST` / `PUT` / `DELETE` either:

- has no observable difference between 1st and Nth invocation (e.g.
  `POST /api/v1/watchlists/{name}/members` — adding a symbol already
  present is a no-op, not an error), OR
- returns `409 Conflict` with `code: 'conflict'` when the state is
  incompatible (e.g. creating a watchlist with a taken name).

Idempotency-Key header support is deferred to FE-11 (SaaS day).

### 3.4 Pagination shape — one envelope for all lists

```python
class Page[T](BaseModel):
    items: list[T]
    cursor: str | None      # opaque; pass back to get next page
    total: int | None       # populated when cheap; else null
```

Routes that return small fixed-size lists (e.g. watchlist members)
keep returning the bare list. Routes that can grow unbounded (trades,
movers, bars, signals) use `Page[T]`.

### 3.5 Time format — ISO 8601 with `Z`

Already the convention in `_ts()` helpers. Pydantic models use
`datetime` with `model_config = ConfigDict(json_encoders={datetime: lambda d: d.isoformat() + 'Z' if d.tzinfo is None else d.isoformat()})`.

### 3.6 Symbols — explicit asset_type field everywhere

Futures (`/MNQM26`) and equities (`AAPL`) share a `symbol` field but
have different rules for normalization, market hours, and display.
Every schema that has a `symbol` ALSO has:

```python
asset_type: Literal["EQUITY", "FUTURE", "OPTION", "INDEX", "FUND"]
```

The frontend's symbol input normalizes on submit (`mnqm26` → `/MNQM26`)
via a shared backend helper exposed through `GET /api/v1/instruments/normalize`.

### 3.7 Versioned namespace — `/api/v1/*`

Locked decision (already in [frontend_plan.md §7.4](frontend_plan.md)):

| Prefix | Audience | Stability |
|---|---|---|
| `/api/v1/...` | External + cockpit | Semver; breaking change = `/api/v2` |
| `/cockpit/...` | Cockpit-only composed endpoints (e.g. `/cockpit/symbol/{ticker}/overview`) | Deploys with the SPA |
| `/mcp/...` | Agent tools | Matched to `/api/v1` |

**Today:** routes live under `/api/`, `/watchlist`, `/monitors`. The
contract pass renames them to `/api/v1/`. Legacy paths get
`RedirectResponse(307)` for the static-HTML transition; deleted when
parity reaches `+1` per [frontend_plan.md §10](frontend_plan.md).

### 3.8 WebSocket — one channel, many topics

`/ws/signals` becomes `/ws/events` (versioned, multiplexed):

```jsonc
// Client → Server
{"op": "subscribe", "topic": "bars.AAPL"}
{"op": "subscribe", "topic": "monitors.*"}
{"op": "unsubscribe", "topic": "bars.AAPL"}

// Server → Client
{"topic": "bars.AAPL", "event": "bar", "data": {...Bar}}
{"topic": "monitors.AAPL", "event": "signal", "data": {...Signal}}
{"topic": "backfill.progress", "event": "tick", "data": {...}}
```

Topic catalog:

| Topic | Event | Payload |
|---|---|---|
| `signals` | `signal` | `Signal` |
| `bars.{symbol}` | `bar` | `Bar` |
| `monitors.{symbol}` | `state` / `signal` / `error` | `MonitorEvent` |
| `backfill.progress` | `tick` | `BackfillProgress` |
| `backtest.{run_id}` | `tick` / `done` | `BacktestProgress` / `RunMetrics` |
| `seed.changed` | `added` / `removed` | `{symbol, asset_type}` |
| `config.changed` | `streaming_provider` | `{from, to}` |
| `logs` | `line` | `LogLine` |

Auth gating (FE-11): the WS handshake reads the same Clerk session
JWT as HTTP routes via `get_principal`.

### 3.9 Read vs write isolation for ad-hoc CH

The CH query page requires special care. Rules:

1. **Connection role.** Use a CH user with `READ ONLY` grants on the
   schema. No DDL, no INSERT, no DELETE.
2. **Row cap.** Query response capped at 10 000 rows (configurable).
   Cap is enforced server-side by appending `LIMIT N` if the user's
   query didn't.
3. **Timeout.** 30 second query timeout. Server kills runaway queries.
4. **Cost-bounded.** `max_memory_usage` and `max_bytes_to_read`
   ClickHouse settings applied per-request.
5. **Allowlist of system tables exposed via `GET /api/v1/clickhouse/schema`.**
   Catalog views like `system.parts` are NOT exposed to avoid leaking
   internal metadata.

---

## 4. Capability ↔ endpoint map (the operator's spec)

For each capability the operator listed, what serves it today and
what's missing.

### 4.1 Search stocks AND futures from a search bar

**Today:** `GET /api/instruments/search?q=` exists, returns
`{query, results: [...], cached: bool}`. Untyped.

**Gaps:**
- No `response_model` → cockpit hand-rolls types.
- No explicit `asset_type` filter (operator wants futures too).
- No normalization endpoint for ambiguous inputs (`mnqm26` → `/MNQM26`).

**Plan:**

```python
class InstrumentMatch(BaseModel):
    symbol: str
    asset_type: Literal["EQUITY", "FUTURE", "OPTION", "INDEX", "FUND"]
    description: str
    exchange: str | None

class InstrumentSearchResponse(BaseModel):
    query: str
    results: list[InstrumentMatch]
    cached: bool
```

Backend: extend the provider call to include futures when
`asset_type` query param is `FUTURE` or unspecified.

New: `GET /api/v1/instruments/normalize?q=mnqm26` → `{symbol: "/MNQM26", asset_type: "FUTURE"}`.

### 4.2 Symbol chart pull-up

**Today:** `GET /api/bars`, `GET /api/signals`, `GET /api/indicators/series`.
Bars + signals untyped; indicators typed.

**Gaps:**
- `Bar` Pydantic model needs to exist; routes need `response_model`.
- Cockpit's hand-rolled `OhlcvBar` + `Signal` + `normalizeBars()`
  shim deletes when this lands.

**Plan:**

```python
class Bar(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None
    trade_count: int | None = None
    interval: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    source: str | None = None   # 'live' | 'ohlcv_1m' | 'ohlcv_5m' | 'ohlcv_daily'

class Signal(BaseModel):
    ts: datetime
    symbol: str
    asset_type: Literal["EQUITY", "FUTURE", "OPTION", "INDEX", "FUND"]
    interval: str
    type: str               # e.g. "regular_bullish_divergence"
    indicator: str          # e.g. "rsi"
    direction: Literal["bull", "bear"]
    price: float
    indicator_value: float | None = None
```

`GET /api/v1/bars` → `Page[Bar]`. `GET /api/v1/signals` → `Page[Signal]`.

### 4.3 Watchlists CRUD

**Today:** `/api/watchlists/*` family exists (8 routes), all
untyped. Plus legacy `/watchlist` single-watchlist routes (4 more).

**Gaps:**
- All untyped; cockpit (FE-3) would re-hand-roll otherwise.
- Legacy `/watchlist` routes should be deleted.
- Mount-prefix inconsistency (§2.4 #1).

**Plan:**

```python
class WatchlistMember(BaseModel):
    symbol: str
    asset_type: Literal[...]
    added_at: datetime
    description: str | None = None

class Watchlist(BaseModel):
    name: str
    kind: Literal["user", "baseline", "adhoc"]
    description: str
    is_active: bool
    created_at: datetime
    member_count: int
    members: list[WatchlistMember] | None = None  # omitted on list endpoints

# Mutations
class CreateWatchlistRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    kind: Literal["user", "baseline", "adhoc"] = "user"
    description: str = Field("", max_length=500)

class AddMembersRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=500)

# Responses
class WatchlistsListResponse(BaseModel):
    watchlists: list[Watchlist]

class WatchlistMutationResponse(BaseModel):
    watchlist: Watchlist
    changed: list[str]    # symbols added or removed (empty if no-op)
```

### 4.4 Seed universe management

**Today:** `SEED_SYMBOLS` is an env var. No HTTP surface. No
mutation path other than editing `.env` and restarting.

**Gaps:**
- No endpoint to read current seed.
- No endpoint to add/remove.
- No persistent backing store survives restart.

**Plan:**

Move seed-symbol persistence from env to a ClickHouse table:

```sql
CREATE TABLE seed_universe (
  symbol String,
  asset_type LowCardinality(String),
  added_at DateTime64(3, 'UTC') DEFAULT now64(),
  added_by String DEFAULT '',
  is_active UInt8 DEFAULT 1,
  notes String DEFAULT ''
) ENGINE = ReplacingMergeTree(added_at)
ORDER BY symbol;
```

Settings reads `seed_symbols` from this table (fallback to env on
empty for first-run bootstrap). Changes emit a `seed.changed` WS
event so the streamer can subscribe/unsubscribe live.

Endpoints:

```python
class SeedEntry(BaseModel):
    symbol: str
    asset_type: Literal[...]
    added_at: datetime
    added_by: str
    notes: str

class SeedUniverseResponse(BaseModel):
    items: list[SeedEntry]
    count: int

# GET    /api/v1/seed
# POST   /api/v1/seed             { symbol, asset_type?, notes? }
# DELETE /api/v1/seed/{symbol}
# POST   /api/v1/seed/import      { symbols: [...] } — bulk
```

**Side-effect contract:** every mutation triggers, in order:
1. CH `seed_universe` upsert.
2. Streamer subscribe / unsubscribe.
3. WS `seed.changed` event broadcast.
4. Backfill enqueue (`POST /api/v1/backfill` daily + intraday) so
   new seed symbols get historical coverage within 30s for chart
   readiness.

### 4.5 Trade journal performance (from Schwab account)

**Today:** `/api/journal/{accounts,trades,summary,sync}` exist.
Journal trades are pulled from Schwab via `journal_sync_service`.
All routes untyped.

**Gaps:**
- Untyped.
- No "performance over time" endpoint (only point-in-time summary).
- No risk metrics (Sharpe, max drawdown, win rate, profit factor).

**Plan:**

```python
class JournalAccount(BaseModel):
    account_hash: str
    nickname: str
    cash_balance: float
    equity: float
    buying_power: float
    open_pnl: float
    day_pnl: float

class JournalTrade(BaseModel):
    account_hash: str
    activity_id: int
    order_id: int
    trade_time: datetime
    symbol: str
    asset_type: Literal[...]
    side: Literal["BUY", "SELL", "BUY_TO_COVER", "SELL_SHORT"]
    position_effect: Literal["OPENING", "CLOSING"]
    quantity: float
    price: float
    gross_amount: float
    fees: float
    net_amount: float
    status: str
    strategy: str
    tags: list[str]
    note: str
    note_updated_at: datetime | None

class JournalSummary(BaseModel):
    window_days: int
    start: datetime
    end: datetime
    realized_pnl: float
    unrealized_pnl: float
    n_trades: int
    n_winners: int
    n_losers: int
    win_rate: float
    profit_factor: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float | None
    avg_winner: float
    avg_loser: float
    by_symbol: list["JournalSymbolBreakdown"]

class JournalSymbolBreakdown(BaseModel):
    symbol: str
    n_trades: int
    realized_pnl: float
    win_rate: float
```

New: `GET /api/v1/journal/equity-curve?account=&days=` →
`{ points: [{ts, equity, realized_pnl, unrealized_pnl}, ...] }`.

### 4.6 Simulated trades — with realistic cost modeling

**Today:** Backtest exists (`POST /api/backtest`, untyped both ways).
The [fees + slippage Protocol framework](../app/services/sim/fees.py)
exists and is already used by the backtester (`FeeModel`,
`SlippageModel`, `make_fees()`, `make_slippage()` registry). **No
paper-trading / sim-trade surface** that uses them.

**Gaps:**
- No `sim_trades` CH table.
- No HTTP surface to place a live sim trade.
- No active-cost-model config (operator picks fees + slippage globally).

**Plan — cost model is first-class:**

Live sim trades route through the **same** `FeeModel` + `SlippageModel`
the backtester uses. The operator picks one of each globally via a
new config endpoint (defaults: `PerShareFees` + `PercentSlippage(0.0005)`).
Every recorded trade carries the audit trail (requested vs filled
price, slippage in dollars + bp, fees) so the operator can review
the cost model's behavior empirically and refine over time.

```sql
CREATE TABLE sim_trades (
  trade_id String,                            -- UUID
  owner_id LowCardinality(String) DEFAULT 'default-tenant',  -- SaaS seam

  ts_placed DateTime64(3, 'UTC'),
  ts_filled DateTime64(3, 'UTC'),

  symbol String,
  asset_type LowCardinality(String),
  side LowCardinality(String),                -- BUY / SELL / SHORT / COVER
  quantity Float64,

  -- Price audit trail
  requested_price Float64,                    -- the last-price the operator saw at click time
  fill_price Float64,                         -- after SlippageModel
  slippage_amount Float64,                    -- fill_price - requested_price (signed by side)
  slippage_bps Float32,                       -- in basis points; signed

  -- Cost audit trail
  fees Float64,                               -- from FeeModel
  fees_model_name LowCardinality(String),     -- e.g. 'per_share' or 'zero'
  slippage_model_name LowCardinality(String), -- e.g. 'percent' or 'next_open'

  -- Net economics
  gross_amount Float64,                       -- quantity * fill_price (signed by side)
  net_amount Float64,                         -- gross_amount - fees

  -- Linkage
  strategy_run_id String DEFAULT '',          -- nullable link to agent_runs
  strategy_name String DEFAULT '',
  source LowCardinality(String),              -- 'manual' / 'agent' / 'backtest'
  note String DEFAULT ''
) ENGINE = MergeTree
ORDER BY (owner_id, ts_placed);
```

Schemas:

```python
# --- core trade record (read shape)

class SimTrade(BaseModel):
    trade_id: str
    ts_placed: datetime
    ts_filled: datetime
    symbol: str
    asset_type: AssetType
    side: Literal["BUY", "SELL", "SHORT", "COVER"]
    quantity: float

    # Price audit
    requested_price: float
    fill_price: float
    slippage_amount: float
    slippage_bps: float

    # Cost audit
    fees: float
    fees_model_name: str
    slippage_model_name: str

    # Net
    gross_amount: float
    net_amount: float

    # Linkage
    strategy_run_id: str | None
    strategy_name: str
    source: Literal["manual", "agent", "backtest"]
    note: str

# --- place a sim trade

class PlaceSimTradeRequest(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL", "SHORT", "COVER"]
    quantity: float = Field(gt=0)
    # If price omitted, fills against the current CH last-price.
    requested_price: float | None = None
    strategy_run_id: str | None = None
    note: str = Field("", max_length=500)

# --- positions + equity

class SimPosition(BaseModel):
    symbol: str
    asset_type: AssetType
    quantity: float
    avg_price: float                       # FIFO basis
    market_price: float | None             # from CH last-price
    unrealized_pnl: float | None
    realized_pnl: float                    # closed-position P&L for this symbol

class SimPositionsResponse(BaseModel):
    as_of: datetime
    cash: float
    equity: float
    realized_pnl_today: float
    unrealized_pnl: float
    positions: list[SimPosition]

class EquityPoint(BaseModel):
    ts: datetime
    equity: float
    drawdown: float

class SimEquityCurveResponse(BaseModel):
    window_days: int
    starting_cash: float
    points: list[EquityPoint]

# --- the active-cost-model config (FE picker)

class CostModelOption(BaseModel):
    name: str                              # 'per_share', 'zero', 'flat_per_trade', ...
    label: str                             # human-readable
    params_schema: dict                    # JSONSchema for constructor params
    is_active: bool
    params: dict                           # current params if active; else defaults

class SimCostConfig(BaseModel):
    fees_model: CostModelOption            # currently active
    slippage_model: CostModelOption        # currently active
    fees_options: list[CostModelOption]    # all available
    slippage_options: list[CostModelOption]

class UpdateSimCostConfigRequest(BaseModel):
    fees_model_name: str | None = None
    fees_model_params: dict | None = None
    slippage_model_name: str | None = None
    slippage_model_params: dict | None = None
```

Endpoints:

```
GET    /api/v1/sim/trades?days=&symbol=        Page[SimTrade]
POST   /api/v1/sim/trades                      → SimTrade (filled)
DELETE /api/v1/sim/trades/{trade_id}           soft-delete

GET    /api/v1/sim/positions                   SimPositionsResponse
GET    /api/v1/sim/equity-curve?days=          SimEquityCurveResponse

GET    /api/v1/sim/cost-config                 SimCostConfig
PUT    /api/v1/sim/cost-config                 UpdateSimCostConfigRequest → SimCostConfig
```

**Fill algorithm (deterministic, audit-able):**

```python
def place_sim_trade(req: PlaceSimTradeRequest, principal: Principal) -> SimTrade:
    fees_model, slippage_model = load_active_cost_models(principal)

    # 1. Resolve requested price (from caller, else CH last-price).
    requested = req.requested_price or get_last_price(req.symbol)

    # 2. Apply slippage (slippage_model.fill_price expects an Action + next_bar;
    #    for instant live fills we synthesize an Action and pass next_bar=None,
    #    which the default models gracefully handle as "fill at requested").
    action = Action(side=req.side, quantity=req.quantity, symbol=req.symbol)
    fill_price = slippage_model.fill_price(action, next_bar=None)
    if fill_price == 0 or fill_price is None:
        fill_price = requested  # safety net

    # 3. Apply fees.
    fees = fees_model.fee_for(action, fill_price)

    # 4. Compute audit numbers + persist.
    slippage_amount = fill_price - requested
    slippage_bps = 10_000 * slippage_amount / requested if requested else 0
    gross = req.quantity * fill_price * (1 if req.side in ("BUY","COVER") else -1)
    net = gross - fees

    return persist_sim_trade(
        trade_id=str(uuid4()),
        owner_id=principal.tenantId,
        symbol=req.symbol,
        ...,
        requested_price=requested,
        fill_price=fill_price,
        slippage_amount=slippage_amount,
        slippage_bps=slippage_bps,
        fees=fees,
        fees_model_name=fees_model.__class__.__name__,
        slippage_model_name=slippage_model.__class__.__name__,
        ...
    )
```

**Refinement path (operator's "more accurate over time" intent):**

Adding realism in the future is a **pure backend** change — the
cockpit's `SimTrade` schema already carries the audit fields. Likely
additions in priority order:

1. `SpreadAwareSlippage` — uses real bid/ask spread (when we have it
   from Schwab quotes) instead of a flat percent.
2. `VolumeAwareSlippage` — scales slippage with `quantity / avg_volume`.
3. `TimeOfDaySlippage` — wider at open/close, tighter mid-day.
4. **Fill-rejection model** — reject orders > X% of recent volume.
   Adds a `status` field (`filled` / `rejected` / `partial`) on
   `SimTrade`.
5. **Partial fills** — break one order into multiple trade rows.

Each lands as a new class implementing `SlippageModel` (or `FeeModel`)
plus a registry entry — zero schema migration, zero frontend change.

### 4.7 Backtest strategies

**Today:** `POST /api/backtest` accepts `dict`, returns `dict`. The
`run_backtest()` function inside is structured; the route just doesn't
declare anything.

**Gaps:**
- Both request and response untyped.
- Doesn't write to `agent_runs` automatically.
- No progress stream.

**Plan:**

```python
class BacktestRequest(BaseModel):
    strategy: str               # e.g. 'sma_crossover_v1'
    params: dict                # validated against strategy.Params schema
    universe: list[str] | Literal["watchlist:default"] | str   # 'watchlist:foo', etc.
    interval: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    start: datetime
    end: datetime
    starting_cash: float = 100_000.0
    fees: str = "default"       # FeesModel id
    snapshot_id: str | None = None    # for replay

class RunMetrics(BaseModel):
    run_id: str
    strategy: str
    strategy_version: str
    start: datetime
    end: datetime
    total_return: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float | None
    n_trades: int
    win_rate: float
    profit_factor: float | None
    final_equity: float

class BacktestResponse(BaseModel):
    metrics: RunMetrics
    equity_curve: list["EquityPoint"]
    trades: list[SimTrade]      # reuse SimTrade shape
    snapshot_id: str

class EquityPoint(BaseModel):
    ts: datetime
    equity: float
    drawdown: float
```

Progress over WS topic `backtest.{run_id}` (§3.8).

### 4.8 Market banner

**Today:** `GET /api/market/banner` exists, untyped.

**Plan:**

```python
class BannerItem(BaseModel):
    symbol: str
    asset_type: Literal[...]
    label: str
    last: float | None
    net_change: float | None
    change_pct: float | None
    close: float | None
    error: str | None = None

class MarketBannerResponse(BaseModel):
    as_of: datetime
    provider: str | None
    items: list[BannerItem]
    errors: list[dict]
```

### 4.9 Daily movers

**Today:** `GET /api/movers` exists, untyped.

**Plan:**

```python
class Mover(BaseModel):
    symbol: str
    asset_type: Literal[...]
    description: str | None
    last: float | None
    net_change: float | None
    change_pct: float | None
    volume: int | None
    trades: int | None
    market_share: float | None
    index: str       # which index this row came from (for fan-out clarity)

class MoversResponse(BaseModel):
    as_of: datetime
    indexes: list[str]
    sort: str
    frequency: int
    items: list[Mover]
```

### 4.10 Indicator overlays on charts

**Today:** `GET /api/indicators/series` AND `POST /api/indicators/chart-data`
both exist and are typed. **No gap here** — this is the gold standard
for how the rest should look.

### 4.11 Streaming-provider config edit

**Today:** `DATA_PROVIDER` env var only. Switching requires editing
`.env` and restarting.

**Gaps:**
- No HTTP surface.
- No persistent override mechanism.
- Hot-swap behavior is unspecified.

**Plan:**

```python
class StreamingConfigResponse(BaseModel):
    current_provider: Literal["alpaca", "polygon", "schwab"]
    supported_providers: list[str]
    last_changed_at: datetime | None
    last_changed_by: str | None
    health: HealthState     # 'ok' | 'warn' | 'error'
    last_message_ts: datetime | None
    subscribed_symbols: int

class ChangeStreamingConfigRequest(BaseModel):
    provider: Literal["alpaca", "polygon", "schwab"]
    confirm_disconnect: bool = False    # safety; explicit ack required

class StreamingConfigChangeResult(BaseModel):
    from_provider: str
    to_provider: str
    disconnected_subscriptions: int
    reconnected_subscriptions: int
    started_at: datetime
    completed_at: datetime
```

**Side-effect contract for `PUT /api/v1/config/streaming`:**

1. Validate target provider has credentials configured (404-equivalent
   if not).
2. Drain the current streamer cleanly.
3. Persist new provider choice to a CH `runtime_config` table
   (overrides env on next read).
4. Start the new streamer, re-subscribe to active universe.
5. Emit WS `config.changed`.
6. Return the result envelope.

**Failure modes:**
- New provider auth fails → revert to old, return 502 with details.
- Drain timeout → return 504 with `details.partial_drain: true`,
  emergency: old streamer is still up.

### 4.12 Ad-hoc ClickHouse query page

**Today:** No endpoint. CH client exists but isn't exposed.

**Gaps:**
- No read-only path.
- No schema introspection endpoint.

**Plan:**

```python
class ClickHouseQueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000)
    max_rows: int = Field(1000, ge=1, le=10_000)
    timeout_seconds: int = Field(30, ge=1, le=120)

class ClickHouseQueryResponse(BaseModel):
    columns: list["CHColumn"]
    rows: list[list]       # JSON-safe values; null for NULLs
    row_count: int
    truncated: bool        # true if hit max_rows cap
    duration_ms: float
    bytes_read: int

class CHColumn(BaseModel):
    name: str
    type: str              # CH type string (UInt64, String, DateTime64(3), ...)

class ClickHouseSchemaResponse(BaseModel):
    tables: list["CHTable"]

class CHTable(BaseModel):
    database: str
    name: str
    engine: str
    row_count: int | None  # cheap estimate, may be null
    columns: list[CHColumn]
```

Routes:

```
POST /api/v1/clickhouse/query    body=ClickHouseQueryRequest, returns ClickHouseQueryResponse
GET  /api/v1/clickhouse/schema   returns ClickHouseSchemaResponse
```

Read-only enforcement: §3.9.

---

## 5. The frontend type-chain after this lands

```
app/api/schemas/         ← NEW: all Pydantic models in one place
├── common.py            (ErrorResponse, Page[T], HealthState, AssetType, ...)
├── bars.py              (Bar)
├── signals.py           (Signal)
├── watchlists.py        (Watchlist, WatchlistMember, ...)
├── seed.py              (SeedEntry, SeedUniverseResponse, ...)
├── journal.py           (JournalAccount, JournalTrade, JournalSummary, ...)
├── sim.py               (SimTrade, SimPosition, ...)
├── backtest.py          (BacktestRequest, BacktestResponse, RunMetrics, ...)
├── config.py            (StreamingConfigResponse, ChangeStreamingConfigRequest, ...)
├── clickhouse.py        (ClickHouseQueryRequest, ClickHouseQueryResponse, ...)
├── market.py            (BannerItem, MarketBannerResponse, Mover, ...)
└── instruments.py       (InstrumentMatch, InstrumentSearchResponse)

   │
   ▼
FastAPI /openapi.json
   │
   │  npm run codegen
   ▼
frontend/src/api/types.gen.ts
   │
   ▼
frontend/src/api/queries.ts     ← migrate from fetch()→raw to apiClient.GET()
   │
   ▼
Components (typed all the way through)
```

The proposed `app/api/schemas/` package follows the **service module
design** memory: one file per concern, importable by both `routes_*`
and (eventually) `mcp/tools/*` so the agent and the cockpit see the
same shapes.

---

## 6. Performance contract (the "fast and responsive" bit)

The cockpit feels fast or slow based on three numbers, two of which
are backend.

| Metric | Target | Where to enforce |
|---|---|---|
| **TTFB on any cockpit list endpoint** | < 50 ms p95 | Per-endpoint test; CH client pool tuning |
| **Initial bundle (gzipped)** | < 250 KB | Vite `vite-bundle-visualizer`; already met (154 KB) |
| **Route transition** | < 100 ms (UI feedback) | React Router code-splitting + skeletons |

### Backend hotpath rules

1. **No N+1 queries.** Routes that need multiple subsystem reads
   `asyncio.gather` them (pattern from `routes_health.py`).
2. **Server-side filtering.** Never return more rows than the
   pagination limit. Cap server-side, return `Page.cursor` for "more."
3. **Time-window queries cap at 100k rows pre-pagination.** Larger
   windows force the cockpit to request smaller windows (cost-control).
4. **Cache the catalog reads.** Instruments search, watchlist member
   lists, ClickHouse schema — all behind in-process caches with
   60-second TTLs (instruments cache pattern already in
   `routes_instruments.py`).

### Frontend hotpath rules

1. **One query per surface; deduplicate via TanStack Query.** The
   `useHealthServices()` pattern (FE-1.5: Status page + StatusBar
   share one round-trip every 10s) is the template.
2. **WebSocket invalidations, not polling.** Once `/ws/events` lands
   (FE-10), poll-based queries get a `subscribeTo` co-hook that
   `queryClient.invalidateQueries(key)` on the right push.
3. **Code-split routes.** Every `routes/*.tsx` becomes a lazy import
   so initial bundle stays under 250 KB.

---

## 7. Recommended phasing

This work doesn't all land at once. Suggested split:

### FE-CONTRACTS-1 — Foundation (~2 days)

- `app/api/schemas/common.py` with `ErrorResponse`, `Page[T]`,
  `AssetType`, `HealthState`, the shared Pydantic primitives.
- Custom exception handler for `ErrorResponse` envelope.
- Frontend `apiClient` middleware that translates non-2xx into typed
  `Result<T, ErrorResponse>`.
- Update `app/api/schemas/` README documenting the rules in §3.

**Gate:** legacy responses unchanged; new envelope ready for new
routes to opt in.

### FE-CONTRACTS-2 — Cockpit-blocking gaps (~3 days)

Routes the cockpit calls TODAY but with hand-rolled types:

- `Bar` + `Signal` models; `/api/v1/bars` + `/api/v1/signals` move
  to `response_model`. Migrate `useSymbolBars`, `useSymbolSignals`,
  delete `normalizeBars()` shim.
- `InstrumentMatch` model; `/api/v1/instruments/search` typed.
- `MarketBannerResponse` model; `/api/v1/market/banner` typed.
- `MoversResponse` model; `/api/v1/movers` typed.

**Gate:** `npm run codegen` produces real types for every endpoint
the existing cockpit pages use. Hand-rolled interfaces in
`queries.ts` deleted.

### FE-CONTRACTS-3 — Watchlists + Monitors (~2 days)

- `Watchlist`, `WatchlistMember`, mutation requests/responses.
- Prefix cleanup (§2.4): `/api/watchlists` → `/api/v1/watchlists`
  (with backward-compat redirects).
- Legacy `/watchlist` (single) routes deprecated with a 6-week
  removal timer.
- `Monitor`, monitor mutation responses; route moves to `/api/v1/monitors`.

**Gate:** FE-3 page can be built with zero hand-rolled types.

### FE-CONTRACTS-4 — Seed universe + provider switch (~3 days)

- `seed_universe` CH table + migration.
- `SeedEntry`, `StreamingConfig*` models.
- `/api/v1/seed`, `/api/v1/config/streaming` endpoints.
- Streamer wired to react to `seed.changed` WS event.
- **This is where the watchlist-vs-streaming question (§10.1) gets
  resolved.**

**Gate:** operator can promote `NVDA` to seed from a curl, see it
appear in the active universe, and confirm Schwab stream subscribes
within 5s.

### FE-CONTRACTS-5 — Sim trades + backtest typing (~3 days)

- `sim_trades` CH table + migration.
- `SimTrade`, `BacktestRequest/Response`, `RunMetrics` models.
- `POST /api/v1/backtest` accepts typed request, returns typed
  response, writes to `agent_runs` automatically.
- `POST /api/v1/sim/trades` family.

**Gate:** backtest run from the FE-4 page produces a row in
`agent_runs` byte-identical to the CLI run (the reproducibility
gate from [trading_subsystem_design.md](trading_subsystem_design.md)).

### FE-CONTRACTS-6 — ClickHouse query + journal typing (~2 days)

- `/api/v1/clickhouse/query` + `/schema` with read-only enforcement.
- Journal models (`JournalAccount`, `JournalTrade`, `JournalSummary`,
  equity curve).
- `GET /api/v1/journal/equity-curve` new endpoint.

**Gate:** Operator can SELECT from `bronze.polygon_minute` via the
CH page and see the row cap honored.

### FE-CONTRACTS-7 — WS events fan-out (~3 days)

`/ws/signals` → `/ws/events` with topic multiplexing. Documented in
§3.8. Replaces all polling.

**Gate:** No HTTP polling on Status, Symbol, Coverage, or Monitors
pages. Pushes update the cockpit within 1s of backend state change.

**Total: ~18 days of focused backend work, threaded with the
parallel frontend phases (FE-3..FE-10).**

---

## 8. Out of scope for this doc

These exist but stay un-touched in the contract pass:

- **MCP tool schemas.** The `mcp/` surface already follows its own
  conventions and gets re-typed when its callers (the agents) need it.
  Cross-pollination from `app/api/schemas/` is welcome but not blocking.
- **OAuth / Schwab refresh flow.** Out of cockpit reach; remains
  CLI-driven for FE-1..FE-10.
- **Internal CLI flags / scripts.** Contract rules apply to HTTP, not
  the internal Python API.

---

## 9. Cost of NOT doing this

If we ship FE-3..FE-13 against today's untyped endpoints:

- ~600 lines of hand-rolled `queries.ts` interfaces ([symptoms today: ~85 lines](../frontend/src/api/queries.ts) for two endpoints).
- Every backend rename silently breaks the frontend at runtime, not build time.
- MCP tools that should share types with the cockpit can't, because
  there's nothing to share.
- SaaS day (FE-11) inherits the debt — every endpoint without a
  schema also lacks an OpenAPI spec the customer can integrate against.

The contract pass costs ~18 days. Skipping it costs ~3 weeks of
debugging-runtime-shape-mismatches and another ~3 weeks of cleanup
before the SaaS launch. Front-loading is the cheaper path.

---

## 10. Decisions — LOCKED 2026-05-18

All seven questions resolved by operator. Recorded here so future
sub-phases don't re-litigate.

### 10.1 Watchlists ≠ streaming subset? — **LOCKED: sticky-universe model**

> **End-to-end implementation locked in
> [`docs/standards/data/symbol_lifecycle.md`](standards/data/symbol_lifecycle.md).**
> That document is the canonical "what happens when a symbol enters
> the system" reference: medallion layering, quick-path warmup,
> standard-path nightlies, latency gate. Read it before touching any
> ingest / universe / backfill code.

**Locked model (the "sticky universe"):**

```
universe = the set of symbols Schwab streams to ClickHouse, 24/7

  Adding a ticker anywhere (search, watchlist, screener)
  that isn't already in the universe
      ↓
  1. Add it to the universe (CH seed_universe table)
  2. Subscribe Schwab CHART_EQUITY stream
  3. Hot-load history: backfill into ClickHouse + S3 bronze
     (~30s tip-fill via Schwab REST)
  4. Silver picks it up the next nightly sync

  Removing from a watchlist:
      ↓
  → does NOT stop streaming. The universe is sticky.
  → Explicit "remove from universe" is a separate action,
    surfaced on a dedicated Seed Universe page (FE-Seed).
```

**Key property: removal is asymmetric.** Watchlists are
organizational labels over the universe; reorganizing watchlists
never loses streaming coverage. To stop streaming a symbol, the
operator must explicitly remove it from the universe.

**Backward compatibility:** today's
[streaming_universe_model.md](streaming_universe_model.md) active-
universe model (`SEED_SYMBOLS ∪ <watchlist members>`) is
**superseded** by this locked model in FE-CONTRACTS-4. The dynamic
union from the streaming model lives on as a *one-time migration*:
on FE-CONTRACTS-4 launch, every symbol in any active watchlist gets
materialized into the new `seed_universe` table. From then on, the
universe is the source of truth, not the watchlists.

### 10.2 `/api/v1` namespace — **LOCKED: one-shot rename**

All `/api/*` → `/api/v1/*` in FE-CONTRACTS-1. Legacy paths return
`RedirectResponse(307)` so the static-HTML pages keep working
through the transition. Legacy redirects deleted in FE-CONTRACTS-7
or when the last legacy HTML page is removed, whichever comes first.

### 10.3 Seed universe storage — **LOCKED: ClickHouse table**

`seed_universe` CH table is the source of truth (DDL in §4.4).
Persistent across restarts, queryable from the CH query page, audit
fields (`added_by`, `added_at`) populate from the request's
`Principal` (the SaaS-readiness seam).

Bootstrap: on first run of FE-CONTRACTS-4, if the CH table is empty,
seed it from today's `SEED_SYMBOLS` env var ∪ current watchlist
members (the one-time migration referenced in §10.1).

### 10.4 ClickHouse query page — **LOCKED: bare SQL + safety rails**

A SQL textarea with results table below. Read-only enforced at the
CH role level (the cockpit connects with a `cockpit_readonly` user
that has SELECT-only grants). Row cap 10 000 (configurable per
request, hard ceiling), 30s query timeout, query log written to
`audit_events`. A "show schema" sidebar provides tab-complete on
table + column names.

### 10.5 Streaming-provider switch — **LOCKED: in-process restart now, hot-swap later**

FE-CONTRACTS-4 ships the in-process restart: cockpit click →
`PUT /api/v1/config/streaming` → persist to `runtime_config` CH
table → kill the streamer → FastAPI lifespan auto-relaunches on the
new provider. ~10s of downtime, no process restart required.

FE-CONTRACTS-7 promotes to true hot-swap once `/ws/events` provides
a pub-sub adapter that can buffer ticks across the swap.

### 10.6 Sim trades — **LOCKED: instant fill UX, with slippage + fees applied**

Hybrid model (the picks-with-modification from operator signoff):

**Fill latency:** instant. No synthetic 100ms delay. Click "Buy
100 AAPL" → trade appears immediately.

**Fill price:** **NOT** the bare last-price. Reuse the existing
[fees + slippage Protocol framework](../app/services/sim/fees.py)
(`FeeModel`, `SlippageModel`, `make_fees()`, `make_slippage()`)
that the backtester already uses. A sim trade picks up the
**same** fees + slippage models the operator selects globally
(default: `PerShareFees` + `PercentSlippage(pct=0.0005)` = 5 bp).

**Why this works:**

- The framework already exists and is unit-tested. No new
  protocol-level design.
- Backtests and live sim trades are now apples-to-apples — the same
  cost assumption that scored a strategy in a backtest applies when
  you paper-trade it live.
- The fields recorded on every `SimTrade` row are explicit
  (`requested_price`, `fill_price`, `slippage_amount`, `slippage_bps`,
  `fees`, `net_amount`) so the operator can audit the cost model's
  behavior over time.
- Refinement happens by swapping the active `SlippageModel` (e.g.
  add a new `SpreadAwareSlippage` later) — components on the cockpit
  side never need to change.

See §4.6 for the updated `SimTrade` schema and the active-cost-model
config endpoint.

### 10.7 MCP parity — **LOCKED: defer**

MCP tools (`app/mcp/tools/*`) keep their current type conventions
through FE-CONTRACTS-*. They migrate to `app/api/schemas/`
piecemeal as each tool gets touched for unrelated reasons. The
cockpit gap is the operator's bottleneck; agent surface parity is
a follow-on phase tracked separately.

---

## 11. Acceptance — what "done" looks like for the contract pass

1. **100% of cockpit-facing routes declare `response_model`.**
   Verified by a one-line grep in CI:
   `! grep -rL 'response_model' app/api/routes_*.py`.
2. **`frontend/src/api/queries.ts` has zero hand-rolled interfaces.**
   All response types come from `types.gen.ts`.
3. **`apiClient` is the only HTTP surface.** No raw `fetch()` in the
   cockpit codebase (enforced by an eslint rule on `src/`).
4. **`ErrorResponse` envelope is universal.** No route returns
   `{"detail": "..."}`.
5. **`/api/v1/*` is the live namespace.** Legacy paths return 307
   redirects with a 6-week deletion timer.
6. **WebSocket is one channel.** `/ws/events` with documented
   topics. `/ws/signals` redirects to `/ws/events?topic=signals` for
   a transition window.
7. **The operator can do every task in §4 from the cockpit, no
   curl.** Verified by a manual walkthrough at the end of
   FE-CONTRACTS-6.

---

## 12. Next action

The contract pass blocks on §10's open questions. Once those are
resolved, FE-CONTRACTS-1 starts. Until then this document is the
**proposal**, not the **plan**.
