# Platform Design — Intent and Principles

This repo is a **modular monolith** being built toward a microservice
platform for **AI/ML-driven day/swing trading on US equities**.

Code-quality bar: every contribution preserves the path to multi-service
deployment without committing to it today.

## Target service map

Long-term target (from [`trading-ai-build-plan.md §5`](../trading-ai-build-plan.md)):

```
feature-server     bronze/silver bars → model features
discovery-jobs     backfills, screening universes, training data prep
screener           picks "interesting today" symbols for agents
simulation         backtest engine
agent-runtime      LLM/RL inference loop
execution          order placement (paper → live)
evaluator          PnL, hit rate, drawdown
monitoring         health, drift, kill switches
api-gateway        REST + MCP entry (today's main_api.py)
```

Eight services. **Today they share one FastAPI process. They become
containers when they need to** — never before, never under pressure.

## Principles (in priority order)

### 1. Contract-first, implementation-last

Every read/write surface starts as a Pydantic schema in `schemas.py`.
Implementations come after. Callers import from `schemas.py` or
`contract.py` (Protocol), never from `service.py`. This is what makes a
service liftable to a container — the boundary is the contract, not the
import graph.

See [`service_modules.md`](service_modules.md) for the folder template
that enforces this.

### 2. Agent-readiness is a first-class concern

Anything an LLM or RL agent might want to read gets:

1. A read service (e.g. `bronze_reader.get_bars`) with a Pydantic contract.
2. An HTTP route in `app/api/` for humans and UI.
3. An MCP tool in `app/mcp/tools/` for agents.

Tools and routes are **thin adapters over the same service**. Never
duplicate logic between the human path and the agent path.

### 3. Lake reads must work without ClickHouse

Historical and training reads go straight to Iceberg via `bronze_reader`
and the future `silver_reader`. ClickHouse is for live and recent data
only. This decouples ML reproducibility from runtime infrastructure — a
training run from six months ago must replay even if ClickHouse is down
or has been redeployed since.

### 4. Three-layer TA: indicators → signals → strategies

- `app/indicators/` — pure math, price → series (RSI, MACD, TSI, …).
  Stateless transformations.
- `app/signals/` — pattern detectors, price + indicator → event
  (divergence, breakouts, MA crossovers, …). Pure functions taking
  tuning knobs as args, never reading `settings`.
- `app/strategies/` (future) — compose multiple signals into a trade
  decision.

Don't conflate. A divergence detector returns an event dict, not a
Series — it's a signal, not an indicator.

### 5. Lake medallion: bronze → silver → gold

- **Bronze** (`stock_lake.{provider}_{kind}`) — raw provider data,
  append-only, one table per (provider, kind). Idempotent: bronze
  appends, never deletes in the hot path. See
  [`data/bronze_idempotency.md`](data/bronze_idempotency.md).
- **Silver** (`stock_lake.ohlcv_1m`) — canonical deduped bars with
  provider-precedence rules and corp actions applied. **This is what
  models train on.** Keep it lean — see
  [`data/lean_silver.md`](data/lean_silver.md).
- **Gold** (`stock_lake.features_*`) — pre-computed feature tables. Each
  training run pins an Iceberg snapshot for replay.

### 6. Hot / cold tier split

- **Hot** (ClickHouse `ohlcv_1m`) — live bars, latency-sensitive UI,
  divergence monitor. Seconds-fresh.
- **Cold** (Iceberg bronze / silver / gold) — ML, backtests, history.
  T+1 freshness today.

The two tiers share no runtime dependencies. Either can be off without
affecting the other.

### 7. Startup isolation

`main_api.py` uses `_safe_start()` for every non-foundation subsystem.
The watchlist failing to authenticate must not block nightly bronze
ingest, HTTP routes, or other subsystems. Foundation tasks (CH schema
init, OHLCV batch writer) stay non-isolated by design — if those fail
the app has nothing useful to serve.

### 8. Reproducibility is non-negotiable

Every saved model logs the Iceberg snapshot ID of the silver and gold
tables it trained on. The `model_training_runs` registry is
authoritative. "Model X trained on data Y" must be re-runnable
bit-for-bit years later.

### 9. Provider abstraction

A provider is a **configuration parameter** (`DATA_PROVIDER`,
`STREAM_PROVIDER`, `HISTORY_PROVIDER`), not a service boundary. Service
folders are organized by **domain** (`bronze/`, `ingest/`, `live/`,
`journal/`, `readers/`), not by provider. Bronze supports multiple
providers via factory methods on one class.

### 10. Doc discipline

The doc layer in `docs/` is part of the build, not an afterthought. See
[`doc_discipline.md`](doc_discipline.md).

## Three test questions for any architectural call

1. **Lift-out test:** can this service still work if it moves to its own
   container next month, talking only over HTTP / MCP? If no, the
   coupling is wrong.
2. **Agent test:** can an LLM agent use this without bespoke
   integration? If no, the Pydantic contract is missing.
3. **Replay test:** if a training run six months from now wants to
   reproduce this data exactly, can it? If no, the snapshot pinning or
   provenance is missing.

If any answer is no, the design is incomplete — surface it before
coding.
