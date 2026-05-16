# StockAlert — System Architecture

> Real-time multi-provider market data ingestion + lakehouse + AI trading
> platform. Modular monolith today, designed to split cleanly into deployable
> services as load demands.

This is the high-level architecture. Two companion docs go deeper:

- [data_platform_plan.md](data_platform_plan.md) — storage + ingestion, Iceberg lake
- [trading-ai-build-plan.md](trading-ai-build-plan.md) — AI trading services on top

---

## 1. Design principles

1. **Service-oriented, deployment-flexible.** Every subsystem is a Python
   package under `app/services/` (or its own top-level package) with a
   well-defined contract (Pydantic models + Protocol). Today they all run
   in one FastAPI process; any of them can be lifted into its own container
   when load or team boundaries demand it.
2. **Contracts at the boundary, freedom inside.** Public interfaces are
   typed Pydantic models. Internal implementation can change without
   coordinating with consumers.
3. **State in shared stores, services are stateless.** ClickHouse holds hot
   data, S3/Iceberg holds the lake, the file system holds models and
   normalizers. Services hold no in-memory state that can't be rebuilt.
4. **Lake is source of truth.** ClickHouse is a rebuildable serving cache;
   if it goes away, replay from Iceberg silver.
5. **One canonical schema per data flow.** Every ingestion path produces
   the same `CanonicalBar`. Every feature path produces the same feature
   vector. No alternate-shape bypaths.
6. **Idempotent everything.** Reruns are no-ops. `MERGE INTO` for the
   lake, `ReplacingMergeTree(version)` for ClickHouse.
7. **Test at the contract.** Each service has unit tests against its
   public interface and integration tests against its real backing store.
   Cross-service tests use fake/in-memory implementations of the contracts.

---

## 2. Tech stack

| Layer | Technology | Role |
|---|---|---|
| Hot store | ClickHouse | Live OHLCV, signals, watchlists, ops/audit tables. Serving cache. |
| Cold store | S3 + Apache Iceberg | Source-of-truth lake (bronze/silver/gold). |
| Catalog | AWS Glue Data Catalog | Iceberg metadata. Zero-ops. |
| Query engines | PyIceberg + DuckDB, Athena | Backtest + ad-hoc SQL. |
| API | FastAPI (async) | REST + WebSocket. App factory in [main_api.py](../app/main_api.py). |
| Streaming | `asyncio` + `websockets` + `aiohttp` | Provider WS adapters. |
| Providers | Polygon, Schwab, Alpaca | Bars (live + REST + flat files). |
| Indicators | `pandas` + custom modules in `app/indicators/` | RSI, MACD, TSI. |
| Config | `pydantic-settings` + `.env` | Type-safe env. [config.py](../app/config.py). |
| AI agents | Anthropic SDK (LLM); Stable-Baselines3 PPO (RL) | Reasoning + learning. |
| Broker | Schwab Trader API | Paper + live order routing. |
| Tooling | Poetry, pytest, Docker Compose | Dev loop. |

---

## 3. Current state (what's built)

The codebase is a FastAPI monolith with internal service modules. As of
today:

```
app/
├── main_api.py              FastAPI app factory; wires startup/shutdown
├── config.py                pydantic-settings, single source of env
│
├── providers/               WS + REST adapters per provider
│   ├── base.py              DataProvider Protocol
│   ├── polygon_provider.py
│   ├── polygon_flatfiles.py
│   ├── schwab_provider.py
│   └── alpaca_provider.py
│
├── db/                      ClickHouse client + DDL + repos
│   ├── client.py            clickhouse-connect singleton
│   ├── init.py              idempotent CREATE TABLE
│   ├── queries.py           typed batch inserts/reads
│   ├── batcher.py           async batcher (500 rows / 5s)
│   ├── lake_watermarks.py   idempotency ledger (becomes ingestion_runs)
│   ├── watchlist_repo.py
│   └── journal_repo.py
│
├── services/                domain-grouped, each with a contract
│   ├── iceberg_catalog.py   shared utility — Glue-backed PyIceberg catalog
│   │
│   ├── bronze/              Iceberg tables (multi-provider via factories)
│   │   ├── schemas.py / tables.py / sink.py / gaps.py / README.md
│   │
│   ├── ingest/              Anything that PUTS data in
│   │   ├── nightly_polygon_refresh.py  daily Polygon → bronze.polygon_minute
│   │   ├── nightly_schwab_refresh.py   daily Schwab  → bronze.schwab_minute
│   │   ├── backfill_service.py         REST gap-fill into CH
│   │   ├── flatfiles_backfill.py       Polygon flat-files → sinks
│   │   ├── historical_loader.py        provider REST chunker
│   │   ├── sinks.py                    Sink Protocol + ClickHouseSink
│   │   └── README.md
│   │
│   ├── live/                Streaming subscription + monitor state
│   │   ├── watchlist_service.py / monitor_service.py / monitor_manager.py
│   │   └── README.md
│   │
│   ├── journal/             Schwab-only account + trade sync
│   │   ├── journal_sync.py / journal_parser.py / pnl.py
│   │   └── README.md
│   │
│   └── legacy/              Pre-Iceberg raw/ writers — Phase 7 removal
│       ├── lake_archive.py / lake_sink.py / s3_lake_client.py
│       └── README.md
│
├── indicators/              technical indicator math
│   ├── base.py
│   ├── rsi.py
│   ├── macd.py
│   └── tsi.py
│
├── api/                     FastAPI routers
│   ├── routes_market.py
│   ├── routes_signals.py
│   ├── routes_watchlist.py
│   ├── routes_monitors.py
│   ├── routes_backfill.py
│   ├── routes_backtest.py
│   ├── routes_instruments.py
│   ├── routes_movers.py
│   └── routes_journal.py
│
├── streamer.py              DivergenceTracker (live signal detection)
├── divergence.py            divergence rules
└── detect_divergence.py
```

Backed by ClickHouse (Docker) and the existing `STOCK_LAKE_BUCKET` S3
bucket. Single FastAPI process today.

---

## 4. Target architecture (services view)

Same code, reorganized as bounded services with explicit contracts.
Several today live in one process; the boundaries below are what each
gets carved into when one becomes its own container.

```
┌──────────────────────────── PRODUCERS (data in) ────────────────────────────┐
│                                                                              │
│  Polygon WS    Polygon REST    Polygon Flat Files    Schwab    Alpaca       │
└──────┬─────────────┬───────────────────┬──────────────┬──────────┬──────────┘
       │             │                   │              │          │
       ▼             ▼                   ▼              ▼          ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  INGEST SERVICES                                                              │
│                                                                                │
│  ingest-stream            ingest-batch              corp-actions-ingest       │
│  ┌──────────────────┐    ┌─────────────────────┐   ┌──────────────────────┐  │
│  │ WS → batcher     │    │ flatfiles + REST    │   │ Polygon splits/divs  │  │
│  │ → CH ohlcv_1m    │    │ → BronzeIcebergSink │   │ → silver.corp_actions│  │
│  └────────┬─────────┘    └──────────┬──────────┘   └──────────┬───────────┘  │
└───────────│─────────────────────────│─────────────────────────│──────────────┘
            │                         │                          │
            ▼                         ▼                          ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAKE                                                                         │
│                                                                                │
│  ClickHouse (hot)              S3 + Apache Iceberg (cold, SOT)               │
│  ┌────────────────────┐        ┌───────────────────────────────────────────┐ │
│  │ ohlcv_1m/5m/daily  │        │  bronze/ (per provider, immutable)         │ │
│  │ signals            │        │  silver/ (canonical, gap-filled, adjusted) │ │
│  │ watchlists         │        │  gold/   (ML features, universes)          │ │
│  │ ingestion_runs     │        │                                            │ │
│  │ account_snapshots  │        │  Glue Data Catalog as Iceberg catalog      │ │
│  │ trades             │        └───────────────────────────────────────────┘ │
│  │ model_training_runs│                                                       │
│  └──────────┬─────────┘                          ▲                            │
└─────────────│────────────────────────────────────│────────────────────────────┘
              │                                    │
              │  live-lake-writer (5min flush)     │
              └────────────────────────────────────┘
                                                   ▲
                                ┌──────────────────┴──────────────────┐
                                │  silver-builder, gold-builder       │
                                │  (offline daily jobs, idempotent)   │
                                └─────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│  SERVING & UI                                                                 │
│                                                                                │
│  api-gateway (FastAPI)               ws-broadcaster                          │
│  ┌─────────────────────────────┐    ┌─────────────────────────────────────┐  │
│  │ REST: /candles /indicators  │    │ WS /ws/live:                         │  │
│  │ /watchlists /signals /...   │    │  candle_update, alert_fired,         │  │
│  │ Auth, rate limit, MCP tools │    │  indicator_update                    │  │
│  └─────────────────────────────┘    └─────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
                                                ▲
                                                │
┌──────────────────────────────────────────────────────────────────────────────┐
│  AI TRADING (see trading-ai-build-plan.md)                                    │
│                                                                                │
│  feature-server   discovery-jobs   agent-runtime   evaluator   execution     │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  ┌────────┐  ┌─────────┐   │
│  │ point-in-   │  │ feature     │  │ LLM + RL   │  │ vector-│  │ Schwab  │   │
│  │ time feats  │  │ importance, │  │ agents.    │  │ bt back│  │ paper/  │   │
│  │ from gold/  │  │ clusters,   │  │ MCP tools. │  │ tests, │  │ live.   │   │
│  │ + silver    │  │ walk-fwd    │  │ Decision   │  │ walk-  │  │ Diverg. │   │
│  │             │  │             │  │ logs       │  │ fwd    │  │ tracker │   │
│  └─────────────┘  └─────────────┘  └────────────┘  └────────┘  └─────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│  CROSS-CUTTING                                                                │
│  config  •  observability (logs, metrics, traces)  •  ingestion_runs audit   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Bounded services (the contract surface)

Each service is a Python package with three files defining its contract:

```
app/services/<name>/
├── schemas.py        Pydantic models (DTOs for inputs/outputs)
├── contract.py       Protocol class — the public interface
├── service.py        Concrete implementation
└── tests/            Contract + integration tests
```

This shape is what makes a service liftable to its own process: the
contract is the only thing callers depend on; the implementation can be
swapped for an HTTP client without changing callers.

### 5.1 ingest-stream

| | |
|---|---|
| **Purpose** | WS feed → normalized `CanonicalBar` → ClickHouse `ohlcv_1m`. |
| **Owns** | Provider WS connections, async batcher, reconnect logic. |
| **Depends on** | ClickHouse, provider adapters. |
| **Contract** | `DataProvider` protocol in [providers/base.py](../app/providers/base.py). |
| **Deploys as** | Today: in-process under FastAPI startup. Future: standalone container per provider. |
| **Failure mode** | Crash restarts; gap-fill backfill closes the hole. |

### 5.2 ingest-batch

| | |
|---|---|
| **Purpose** | Polygon flat files + REST gap-fill → bronze Iceberg tables. |
| **Owns** | [flatfiles_backfill.py](../app/services/flatfiles_backfill.py), [backfill_service.py](../app/services/backfill_service.py), [nightly_lake_refresh.py](../app/services/nightly_lake_refresh.py). |
| **Depends on** | Provider REST/S3, Iceberg catalog. |
| **Contract** | `BackfillJob` (Pydantic): `{symbol, start, end, depth, priority}` → `JobResult`. |
| **Deploys as** | In-process worker today; future: cron job in a Kubernetes CronJob or Argo workflow. |
| **Idempotency** | Iceberg `MERGE INTO` + `ingestion_runs` audit. |

### 5.3 live-lake-writer

| | |
|---|---|
| **Purpose** | Every 5 min, drain CH `ohlcv_1m` → `bronze.{provider}_minute`. |
| **Owns** | The CH → Iceberg flush job. |
| **Depends on** | ClickHouse, Iceberg catalog. |
| **Contract** | None public; runs on a timer. |
| **Deploys as** | Background asyncio task today; future: separate container with its own schedule. |
| **Failure mode** | Reruns are safe (`MERGE INTO`). Watchdog alarms if no run in 15 min. |

### 5.4 silver-builder

| | |
|---|---|
| **Purpose** | Daily: bronze (all providers) → `silver.ohlcv_1m / daily / bar_quality`. |
| **Owns** | Provider precedence, gap-fill, raw + adjusted price computation, QA stats. |
| **Depends on** | Iceberg catalog only. Stateless. |
| **Contract** | CLI: `silver_build --date YYYY-MM-DD`. |
| **Deploys as** | Cron container. |
| **Idempotency** | `MERGE INTO`. Rerunning a date is a no-op unless bronze changed. |

### 5.5 gold-builder

| | |
|---|---|
| **Purpose** | Daily: silver → `gold.features_1m / daily / universes`. |
| **Owns** | Feature definitions, versioned via `feature_set_version`. |
| **Depends on** | Iceberg catalog. |
| **Contract** | CLI: `gold_build --date YYYY-MM-DD --feature-set v3`. |
| **Deploys as** | Cron container. |

### 5.6 corp-actions-ingest

| | |
|---|---|
| **Purpose** | Pull Polygon splits/dividends → `silver.corp_actions`. |
| **Owns** | One provider (Polygon) for corp actions; no normalization across providers needed. |
| **Depends on** | Polygon API, Iceberg catalog. |
| **Contract** | CLI: `corp_actions_sync --since DATE`. |
| **Deploys as** | Cron container (daily). |

### 5.7 ws-broadcaster

| | |
|---|---|
| **Purpose** | Push live bars, indicator updates, fired alerts to the React UI. |
| **Owns** | WebSocket connection manager, fan-out queue. |
| **Depends on** | In-process pub/sub from streamer + signal detector. |
| **Contract** | WS messages: `{type: "candle_update" \| "alert_fired" \| "indicator_update", symbol, data}`. |
| **Deploys as** | Co-located with FastAPI today; could be lifted to a Redis pub/sub fan-out service later. |

### 5.8 api-gateway

| | |
|---|---|
| **Purpose** | Single REST/MCP entry point for UI + agents. |
| **Owns** | Routes, auth, request validation. No business logic — calls services. |
| **Depends on** | Every other service via contract. |
| **Contract** | OpenAPI from FastAPI; MCP tools from `server/mcp_tools/` (per trading-ai plan). |
| **Deploys as** | Always its own process. Existing [main_api.py](../app/main_api.py). |

### 5.9 feature-server

| | |
|---|---|
| **Purpose** | Point-in-time feature vectors for agents. |
| **Owns** | Feature fetch + normalizer cache. |
| **Depends on** | Gold layer (Iceberg) + the persisted RobustScaler. |
| **Contract** | `get_features(symbol, ts, lookback) → np.ndarray`. |
| **Deploys as** | In-process call today (DuckDB + Iceberg); future: gRPC/HTTP service if call rates demand caching. |

### 5.10 agent-runtime

| | |
|---|---|
| **Purpose** | LLM + RL inference loop. |
| **Owns** | Decision logs, model artifacts, training pipeline. |
| **Depends on** | feature-server, execution, evaluator. |
| **Contract** | Per-agent: `decide(observation) → Action`. |
| **Deploys as** | Standalone container. Detail in [trading-ai-build-plan.md](trading-ai-build-plan.md). |

### 5.11 execution

| | |
|---|---|
| **Purpose** | Order routing abstraction. Sim ↔ Schwab paper ↔ Schwab live behind one interface. |
| **Owns** | Schwab API client, simulator, divergence tracker. |
| **Depends on** | Schwab API; CH for trade log. |
| **Contract** | `ExecutionInterface.place_order(symbol, side, size) → FillResult`. |
| **Deploys as** | Standalone container. |

### 5.12 evaluator

| | |
|---|---|
| **Purpose** | Backtests, walk-forward validation, paper-vs-sim divergence reports. |
| **Owns** | VectorBT integration, walk-forward engine, metric calculators. |
| **Depends on** | silver + gold + decision logs. |
| **Contract** | `run_backtest(model, params, window) → BacktestReport`. |
| **Deploys as** | On-demand worker. |

### 5.13 ops/observability

Single ClickHouse `ingestion_runs` table (audit) plus structlog → stdout
→ container log aggregation. No fancy infra until needed.

---

## 6. Data flow scenarios

### 6.1 Live bar lands in the lake

```
Polygon WS message
  → polygon_provider._parse_bar()
  → CanonicalBar(symbol, ts, ohlcv, provider="polygon", payload_hash, ...)
  → streamer.py (computes indicators, evaluates rules)
  → batcher → ClickHouse.ohlcv_1m (T+0)
  → ws-broadcaster → React UI

[every 5 minutes]
  live-lake-writer
  → SELECT FROM ohlcv_1m WHERE ts > last_flush
  → MERGE INTO bronze.polygon_minute
  → record ingestion_runs row
```

### 6.2 Nightly backfill + silver build

```
07:00 UTC
  nightly_lake_refresh
  → PolygonFlatFilesClient.fetch(yesterday)
  → CanonicalBar batches
  → BronzeIcebergSink.MERGE INTO bronze.polygon_minute / polygon_day

08:00 UTC
  silver-builder --date=yesterday
  → read bronze.*_minute / *_day for date
  → apply provider precedence (config: polygon > schwab > alpaca)
  → join silver.corp_actions, compute raw + adjusted columns
  → MERGE INTO silver.ohlcv_1m / silver.ohlcv_daily
  → compute QA stats → MERGE INTO silver.bar_quality
  → record ingestion_runs row

08:30 UTC
  gold-builder --date=yesterday
  → read silver.ohlcv_1m
  → compute returns, vol, indicators (feature_set_version=v3)
  → MERGE INTO gold.features_1m
```

### 6.3 Agent inference

```
agent-runtime tick
  → feature-server.get_features("AAPL", ts=now, lookback=20)
    → DuckDB on gold.features_1m + position state
  → np.ndarray (1004-dim observation)
  → rl_agent.predict(obs) → action ∈ {hold, buy, sell}
  → execution.place_order(...)
    → Sim: trade_simulator
    → Paper/Live: Schwab API
  → divergence_tracker compares sim vs paper
  → decision_logger writes to CH model_training_runs / decisions table
```

### 6.4 ML training reproducibility

```
training run starts
  → silver_snapshot = iceberg.table("silver.ohlcv_1m").current_snapshot()
  → gold_snapshot   = iceberg.table("gold.features_1m").current_snapshot()
  → tag both: model_<run_id>_silver, model_<run_id>_gold (never expire)
  → write CH.model_training_runs row with both snapshot IDs + code git sha + params
  → train
  → save artifact + metrics

later (debug / retrain)
  → read CH.model_training_runs
  → query silver/gold AS OF the tagged snapshot
  → reproduce training set bit-identical
```

---

## 7. Cross-cutting concerns

### 7.1 Configuration

Single `Settings` class in [config.py](../app/config.py) via `pydantic-settings`.
Sources: `.env` → environment variables → defaults. Subsystems read a
typed slice, never `os.environ` directly.

Service-local config (reward weights, training hyperparams) lives in
`config/<service>_config.py` files but loads through the same `Settings`
machinery, so all knobs are discoverable from one place.

### 7.2 Observability

- **Logs:** `structlog`, JSON to stdout. Container runtime collects.
- **Metrics:** counters and gauges via Prometheus client when we add it;
  for now, `ingestion_runs` + decision logs in CH cover what we need.
- **Audit:** every write to the lake records a row in `ingestion_runs`
  with `snapshot_id_before / snapshot_id_after`, linking ops to data state.
- **Traces:** deferred until cross-service calls exist.

### 7.3 Schema management

- **ClickHouse:** [db/init.py](../app/db/init.py) is idempotent (`CREATE TABLE IF NOT EXISTS`).
  Changes go in as additive migrations.
- **Iceberg:** schema evolution via PyIceberg `add_column`. Never rename
  columns (readers may pin old snapshots).
- **Pydantic contracts:** versioned by package. Breaking changes require
  a new contract module (`schemas_v2.py`); old callers keep working.

### 7.4 Testing layers

| Layer | What it tests | Where |
|---|---|---|
| Unit | Pure functions (indicator math, parsers, reward components) | per-module `tests/` |
| Contract | Service Protocol upheld by its impl | per-service `tests/contract_test.py` |
| Integration | Service against real ClickHouse / Iceberg | `tests/integration/` (gated by env var) |
| End-to-end | Live → bronze → silver → feature → agent | `tests/e2e/` (rare, in CI nightly) |

The contract layer is what lets us swap real services for fakes in
end-to-end tests — no module talks directly to the impl of another, only
to its Protocol.

### 7.5 Security & secrets

- Secrets in `.env` locally, AWS Secrets Manager / SSM Parameter Store
  in production.
- Schwab OAuth refresh token lives at a single configured path
  (`SCHWAB_REFRESH_TOKEN_FILE`) — only the execution service reads it.
- S3 bucket policy: IAM scoped per service to the prefix it needs.

---

## 8. Deployment topology

### 8.1 Local development

One Docker Compose stack, one FastAPI process, ClickHouse in a container.
See [docker-compose.yml](../docker-compose.yml).

```
docker compose --profile full up
  → clickhouse:8123
  → api:8000  (FastAPI + all in-process services)
```

The S3 lake points at the real bucket (or LocalStack for offline work).

### 8.2 Production (target)

Each bounded service runs in its own container. Same code; different
entrypoint per service. Orchestration is fine on AWS ECS, Fargate, or
Kubernetes.

```
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ api-gateway         │  │ ingest-stream       │  │ ingest-batch        │
│ (FastAPI)           │  │ (asyncio worker)    │  │ (cron worker)       │
│ public LB → 443     │  │ no inbound          │  │ no inbound          │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
            │                       │                        │
            └───────────────────────┼────────────────────────┘
                                    ▼
                  ┌────────────────────────────────┐
                  │ ClickHouse (managed)           │
                  │ S3 stock-lake (Iceberg + Glue) │
                  └────────────────────────────────┘
                                    │
            ┌───────────────────────┼────────────────────────┐
            ▼                       ▼                        ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ silver-builder      │  │ gold-builder        │  │ agent-runtime       │
│ (daily cron)        │  │ (daily cron)        │  │ (long-running)      │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
                                                              │
                                                              ▼
                                                  ┌─────────────────────┐
                                                  │ execution           │
                                                  │ (Schwab API)        │
                                                  └─────────────────────┘
```

Each container has its own resource profile:
- ingest-stream: CPU-light, network-heavy, always-on
- ingest-batch / silver-builder / gold-builder: CPU-heavy, run-to-completion
- agent-runtime: GPU optional, long-running
- api-gateway: scaled horizontally behind LB

### 8.3 Promotion path (sim → paper → live)

A single environment variable on `execution` flips the backend.
Everything upstream is identical. Detail in [trading-ai-build-plan.md](trading-ai-build-plan.md#promotion-path).

---

## 9. Key conventions

- **UTC everywhere.** Display-layer conversion only.
- **CanonicalBar is the only bar shape.** Adapters normalize at the edge;
  nothing downstream knows which provider a bar came from (except via the
  `source_provider` column).
- **`LowCardinality(String)` for symbol and provider in CH.**
- **`IF NOT EXISTS` on all DDL.** [db/init.py](../app/db/init.py) runs on
  every startup safely.
- **Batch all CH inserts.** Never row-at-a-time. Batcher: 500 rows / 5s.
- **`MERGE INTO` for all Iceberg writes.** Never `OVERWRITE` outside of
  compaction.
- **Pydantic for all I/O.** Boundary validation; internal code can trust
  types.
- **No magic numbers in module code.** Every threshold or limit in
  `config/`.

---

## 10. What changes from the previous architecture

This doc replaces the older `ARCHITECTURE.md`, which described an
in-process monolith with ClickHouse as source of truth. The substantive
shifts:

1. **Lake is the source of truth.** ClickHouse demoted to serving cache.
   (See [data_platform_plan.md](data_platform_plan.md).)
2. **Iceberg, not raw Parquet.** ACID writes, schema evolution, time
   travel, MERGE INTO. Glue as the catalog.
3. **Medallion layout.** bronze (per-provider, immutable) → silver
   (canonical, gap-filled, dual raw/adjusted) → gold (ML features).
4. **Service boundaries explicit.** Each subsystem has a Pydantic
   contract; can be lifted to its own container without rewriting
   callers.
5. **Reproducible ML training.** Iceberg snapshot pinning for every
   saved model; `model_training_runs` registry in CH.
6. **`ingestion_runs` replaces `lake_archive_watermarks`.** Audit-only
   table; Iceberg `MERGE INTO` is the correctness guarantee.

Migration plan for the lake side is in
[data_platform_plan.md §13](data_platform_plan.md). Migration plan for
the AI trading side is in
[trading-ai-build-plan.md](trading-ai-build-plan.md).
