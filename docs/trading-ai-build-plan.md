# AI Trading — Build Plan

> A modular, service-oriented AI trading system layered on the StockAlert
> data platform. Each capability is a deployable service with a typed
> contract; local dev runs them in-process, production deploys each as its
> own container.

See [ARCHITECTURE.md](ARCHITECTURE.md) for system-wide context and
[data_platform_plan.md](data_platform_plan.md) for the data lake this
plan reads from.

---

## Table of Contents

1. [Goals & non-goals](#1-goals--non-goals)
2. [Design principles](#2-design-principles)
3. [Service map](#3-service-map)
4. [The service template](#4-the-service-template)
5. [Service specs](#5-service-specs)
6. [Inter-service contracts](#6-inter-service-contracts)
7. [Data flow](#7-data-flow)
8. [Agent learning strategy](#8-agent-learning-strategy)
9. [Reward engineering](#9-reward-engineering)
10. [Deployment topology](#10-deployment-topology)
11. [Build phases](#11-build-phases)
12. [Validation gates](#12-validation-gates)
13. [Risk controls](#13-risk-controls)
14. [Tech stack](#14-tech-stack)

---

## 1. Goals & non-goals

### Goals

- Layer four capabilities onto the existing platform: **features →
  discovery → agents → execution**.
- Start the simulated agent with **$40,000** of capital, gated by hard
  risk controls.
- An agent that takes **fewer, higher-probability trades over time**.
- Every model training run is **bit-reproducible** via Iceberg snapshot
  pinning ([data_platform_plan §10](data_platform_plan.md#10-ml-reproducibility)).
- Promotion path: **sim → Schwab paper → Schwab live** with the same code.
- Each capability is a **deployable service** — testable in isolation,
  scaled independently, swappable for a fake in tests.

### Non-goals (explicit)

- **Tick / quote data.** Bars-only.
- **Options, futures, crypto.** Equities only for now.
- **Sub-second feature serving.** 5-min cadence is sufficient.
- **Cross-strategy capital allocation.** One agent, one capital pool.
- **Self-hosted feature store (Feast, etc.).** PyIceberg + DuckDB is
  enough at this scale.

---

## 2. Design principles

1. **Contract-first.** Every service exposes a Pydantic Protocol. Callers
   depend only on the contract; implementations swap freely.
2. **Stateless services, stateful stores.** State in CH (ops), Iceberg
   (data), disk (models + normalizers). No in-memory state that can't be
   rebuilt.
3. **No encoded bias.** Compute every indicator; never hard-code "RSI > 30
   means buy." Let the agent learn weights from data.
4. **No future leakage, ever.** At decision time `t`, features see bars
   `[t-N, t]` only. Enforced in the feature contract.
5. **Liquidity gates only.** The only hard filters are physical
   execution constraints. Everything else is learned.
6. **Reward quality, not quantity.** Reward function explicitly punishes
   overtrading and rewards patience.
7. **R-multiples over dollars.** Profit-per-unit-risk normalizes across
   position sizes and capital levels.
8. **Same code path sim → paper → live.** Switching execution targets is
   a config flag.
9. **Validate at every gate.** Promotion to the next phase requires
   measurable criteria.
10. **The last 6 months of data is sacred.** Untouched during training.

---

## 3. Service map

```
                    ┌─────────────────────────────────────┐
                    │  data platform (existing)            │
                    │  silver.ohlcv_1m, gold.features_1m   │
                    │  silver.corp_actions, ingestion_runs │
                    └──────────────┬──────────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌─────────────────┐      ┌─────────────────┐         ┌─────────────────┐
│ feature-server  │      │ discovery-jobs  │         │ screener        │
│  point-in-time  │      │  importance,    │         │  liquidity      │
│  features       │      │  clusters,      │         │  filters        │
│                 │      │  walk-forward   │         │                 │
└────────┬────────┘      └────────┬────────┘         └────────┬────────┘
         │                        │                            │
         │ feature vectors        │ importance / cluster       │ tradeable
         │                        │ artifacts                  │ universe
         ▼                        ▼                            ▼
                  ┌─────────────────────────────────┐
                  │ agent-runtime                    │
                  │  ┌─────────┐  ┌───────────────┐ │
                  │  │ LLM     │  │ RL (PPO)      │ │
                  │  │ agent   │  │ agent + env   │ │
                  │  └─────────┘  └───────────────┘ │
                  │  decision_logger                 │
                  └─────────────┬───────────────────┘
                                │ actions
                                ▼
                  ┌─────────────────────────────────┐
                  │ execution                        │
                  │  ┌──────────┐  ┌───────────┐    │
                  │  │ simulator│  │ schwab-   │    │
                  │  │          │  │ client    │    │
                  │  └──────────┘  └───────────┘    │
                  │  divergence_tracker              │
                  └─────────────┬───────────────────┘
                                │ fills, pnl
                                ▼
                  ┌─────────────────────────────────┐
                  │ evaluator                        │
                  │  vectorbt, walk-forward, metrics │
                  └─────────────────────────────────┘
                                │
                                ▼
                  ┌─────────────────────────────────┐
                  │ monitoring                       │
                  │  trade quality, drawdown alerts  │
                  └─────────────────────────────────┘
                                │
                                ▼
                  ┌─────────────────────────────────┐
                  │ api-gateway (existing)           │
                  │  MCP tools, REST                 │
                  └─────────────────────────────────┘
```

Each box is a deployable service. Arrows are typed contracts; nothing
crosses a boundary except via a `schemas.py` model.

---

## 4. The service template

Every service follows the same shape so any of them is liftable to its
own container without rework:

```
trading_ai/<service-name>/
├── __init__.py
├── README.md             What it does, what it owns, contract, how to test
├── schemas.py            Pydantic DTOs (input + output models)
├── contract.py           Protocol class — public interface
├── service.py            Implementation
├── client.py             HTTP client conforming to the same Protocol
├── main.py               FastAPI entrypoint exposing the contract
├── config.py             Service-local settings (loaded from global Settings)
└── tests/
    ├── test_contract.py  Verifies impl satisfies Protocol
    ├── test_service.py   Unit tests
    └── test_integration.py  Real backing store (gated)
```

`README.md` is non-optional. New service → README in the same change
as the code (see [docs/README.md working agreement](README.md)).

### Why this shape

- **`schemas.py`** is the only file consumers depend on. Importing it
  from another service is fine; importing `service.py` is not.
- **`contract.py`** lets you write fake implementations for tests:
  `FakeFeatureServer(FeatureServerProtocol)`. Agent tests never call the
  real feature pipeline.
- **`client.py`** is a thin HTTP wrapper around the same Protocol. When
  you lift the service to its own container, callers swap `service.py`
  for `client.py` via dependency injection — zero changes elsewhere.
- **`main.py`** is the FastAPI bootstrap when running standalone. Reuses
  the same `service.py` instance.

### Example: minimal feature-server skeleton

```python
# trading_ai/feature_server/schemas.py
from pydantic import BaseModel
from datetime import datetime
import numpy as np

class FeatureRequest(BaseModel):
    symbol: str
    as_of: datetime          # decision time t — no bar after this
    lookback_bars: int = 20

class FeatureResponse(BaseModel):
    symbol: str
    as_of: datetime
    feature_set_version: str
    values: list[list[float]]  # shape: [lookback, n_features]
    gold_snapshot_id: int      # for reproducibility

# trading_ai/feature_server/contract.py
from typing import Protocol
from .schemas import FeatureRequest, FeatureResponse

class FeatureServerProtocol(Protocol):
    async def get_features(self, req: FeatureRequest) -> FeatureResponse: ...

# trading_ai/feature_server/service.py
class FeatureServer:
    def __init__(self, iceberg_catalog, scaler_path: str): ...
    async def get_features(self, req: FeatureRequest) -> FeatureResponse:
        # Query gold.features_1m via PyIceberg + DuckDB
        # Apply persisted normalizer
        # Return shape-validated response
        ...

# trading_ai/feature_server/client.py
import httpx
class FeatureServerClient:
    def __init__(self, base_url: str): ...
    async def get_features(self, req: FeatureRequest) -> FeatureResponse:
        r = await httpx.AsyncClient().post(f"{self.base_url}/get_features", json=req.model_dump())
        return FeatureResponse.model_validate(r.json())
```

Both `FeatureServer` and `FeatureServerClient` satisfy
`FeatureServerProtocol`. Callers don't care which they got.

---

## 5. Service specs

### 5.1 feature-server

| | |
|---|---|
| **Purpose** | Point-in-time feature vectors. The only source of input data for agents. |
| **Owns** | Feature fetch from `gold.features_1m`, normalizer cache, leakage enforcement. |
| **Depends on** | Iceberg catalog, persisted RobustScaler at known path. |
| **Reads** | `gold.features_1m`, `gold.universes`. |
| **Contract** | `get_features(req: FeatureRequest) → FeatureResponse`. |
| **Key invariant** | `as_of` is exclusive — never returns bars > `as_of`. |
| **Failure mode** | Returns 4xx if symbol/date has insufficient history. Never returns NaN. |

### 5.2 discovery-jobs

| | |
|---|---|
| **Purpose** | Offline analysis. Outputs artifacts the LLM agent reads. |
| **Owns** | `feature_importance.py` (RF on forward returns), `cluster_discovery.py` (KMeans regimes), `walk_forward_validator.py`. |
| **Depends on** | feature-server (historical mode), silver.ohlcv_1m for forward labels. |
| **Writes** | `s3://stock-lake/discovery/{date}/feature_importance.json`, `cluster_model.pkl`. |
| **Contract** | CLI: `run_discovery --as-of YYYY-MM-DD`. |
| **Cadence** | Quarterly refresh. |
| **Reproducibility** | Records the silver/gold snapshot IDs used; tag them. |

### 5.3 screener

| | |
|---|---|
| **Purpose** | Liquidity filter. The only place with hard rules — physical execution feasibility. |
| **Owns** | `liquidity_filter.py`. |
| **Depends on** | silver.ohlcv_daily, optionally a fundamentals feed for earnings dates. |
| **Contract** | `get_tradeable_universe(date: date) → list[Symbol]`. |
| **Gates** | 20-day avg dollar volume > $5M, ATR% in [1.5%, 8%], price in [$1, $500], not an earnings day, ≥252 bars of history. |
| **Output target** | `gold.universes` (daily point-in-time snapshot). |

### 5.4 simulation

| | |
|---|---|
| **Purpose** | In-memory training environment. No network calls. |
| **Owns** | `TradeSimulator`, `RewardEngine`, `CircuitBreaker`. |
| **Depends on** | Nothing at runtime (gym-style stepper). |
| **Contract** | Implements `ExecutionInterface` (same as Schwab client). |
| **Knobs** | Slippage (5 bps default), commission (0.05%), max position size, drawdown halt (15%). |
| **Reward** | Composite of R-multiple, transaction cost, patience, overtrade, drawdown — see [§9](#9-reward-engineering). |

### 5.5 agent-runtime

| | |
|---|---|
| **Purpose** | Decide. LLM and RL agents share the same observation/action contract. |
| **Owns** | `LLMAgent`, `RLEnv`, `RLAgent` (PPO), `HybridAgent`, `DecisionLogger`. |
| **Depends on** | feature-server, execution, discovery artifacts. |
| **Contract** | `decide(obs: Observation) → Action`. |
| **Reproducibility** | Every saved model carries `silver_snapshot_id`, `gold_snapshot_id`, `feature_set_version`, `code_git_sha`. |

### 5.6 execution

| | |
|---|---|
| **Purpose** | Order routing abstraction. Sim / paper / live behind one interface. |
| **Owns** | `SchwabClient`, `Simulator` (re-export), `DivergenceTracker`. |
| **Depends on** | Schwab API (paper + live modes). |
| **Contract** | `place_order(req: OrderRequest) → FillResult`, `cancel_order(id) → CancelResult`, `get_positions() → list[Position]`, `get_pnl() → PnLSnapshot`. |
| **Switch** | `EXECUTION_BACKEND=sim|paper|live` env var. Same code path. |
| **Hard limits** | Position size cap, drawdown circuit breaker, max trades/day — enforced inside this service, not at the caller. |

### 5.7 evaluator

| | |
|---|---|
| **Purpose** | Backtest, walk-forward, divergence analysis. |
| **Owns** | VectorBT runner, walk-forward engine, metric calculators. |
| **Depends on** | silver, gold, decision_logger output. |
| **Contract** | `run_backtest(model_id, params, window) → BacktestReport`. |
| **Outputs** | `BacktestReport` written to `gold.backtest_runs` + artifact in S3. |

### 5.8 monitoring

| | |
|---|---|
| **Purpose** | Real-time visibility into agent behavior + capital risk. |
| **Owns** | Trade-quality metric computation, alert rules, sim-vs-live divergence. |
| **Depends on** | decision_logger output, fills from execution. |
| **Contract** | `get_metrics(model_id, window) → MetricsSnapshot`. |
| **Outputs** | Webhook alerts on drawdown / overtrading / divergence breaches. |

### 5.9 api-gateway (extends existing)

Adds MCP tools to the existing FastAPI MCP server. Each tool is a thin
wrapper that calls one of the services above via its Protocol.

| MCP tool | Calls service |
|---|---|
| `get_features` | feature-server |
| `get_universe` | screener |
| `place_order` | execution |
| `cancel_order` | execution |
| `get_positions` | execution |
| `get_pnl` | execution |
| `run_backtest` | evaluator |
| `get_feature_importance` | (reads discovery artifact) |
| `get_clusters` | (reads discovery artifact) |
| `get_decision_log` | agent-runtime |

---

## 6. Inter-service contracts

Every contract is a `Protocol` in the service's `contract.py`. Cross-
service imports allowed only from `schemas.py` or `contract.py` —
never `service.py`. Enforced in code review.

### 6.1 Core DTOs

```python
class Observation(BaseModel):
    symbol: str
    as_of: datetime
    feature_window: list[list[float]]   # [lookback, n_features]
    position_state: PositionState        # size, unrealized_pnl, bars_held, cash_ratio
    feature_set_version: str
    gold_snapshot_id: int                # for reproducibility

class Action(BaseModel):
    kind: Literal["hold", "buy", "sell"]
    rationale: str | None = None         # set by LLM agent; None for RL

class OrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None
    client_order_id: UUID                 # for idempotency

class FillResult(BaseModel):
    client_order_id: UUID
    status: Literal["filled", "partial", "rejected", "pending"]
    filled_quantity: int
    avg_fill_price: float
    fees: float
    timestamp: datetime
    reason: str | None = None

class Decision(BaseModel):
    run_id: UUID
    timestamp: datetime
    observation: Observation
    action: Action
    fill: FillResult | None
    reward: float | None
    model_id: str
    code_git_sha: str
```

### 6.2 Versioning

- Schemas are versioned by module path (`schemas_v1.py`, `schemas_v2.py`).
- Adding optional fields is non-breaking; renaming / removing requires a
  new version.
- Services publish their schema version in the FastAPI `/version`
  endpoint; clients pin to a known version.

---

## 7. Data flow

### 7.1 Training loop (sim, $40k starting capital)

```
1. Episode reset
   ├── screener.get_tradeable_universe(episode_date) → list of symbols
   ├── Pick random symbol + random start bar (sufficient history both sides)
   ├── simulator.reset(cash=$40,000)
   └── observation_window = bars [start_bar-19, start_bar]

2. Per-step loop at bar t:
   ├── feature-server.get_features(symbol, as_of=t, lookback=20)
   │       → returns FeatureResponse (1000-dim normalized)
   ├── Observation = features + simulator.get_position_state()
   ├── agent.decide(observation) → Action
   ├── if action != hold:
   │       └── simulator.place_order(...) → FillResult
   ├── reward_engine.compute_reward(action, fill, state) → float
   ├── decision_logger.log(Decision(...))
   ├── t += 1
   └── done = (t == end) or (drawdown > 15%)

3. End of episode:
   ├── PPO update on collected experience
   ├── tensorboard.log(reward, trade_count, win_rate, final_equity)
   └── Repeat
```

### 7.2 Inference loop (sim / paper / live)

Identical, just `EXECUTION_BACKEND` flipped:

```
[every minute on the minute, market hours]
  feature-server.get_features(symbol, as_of=now, lookback=20)
  agent.decide(observation)
  if action != hold:
    execution.place_order(OrderRequest(..., client_order_id=uuid4()))
  monitoring.divergence_tracker.observe(action, fill)
  decision_logger.log(Decision(...))
```

### 7.3 Training run lifecycle (reproducibility-enabled)

```
1. trainer starts a run:
   - run_id = uuid4()
   - silver_snap = iceberg.table("silver.ohlcv_1m").current_snapshot()
   - gold_snap   = iceberg.table("gold.features_1m").current_snapshot()
   - tag both: model_<run_id>_silver, model_<run_id>_gold (never expire)
   - INSERT INTO model_training_runs (run_id, silver_snapshot_id, gold_snapshot_id,
                                       feature_set_version, code_git_sha, params)

2. trainer pins all reads:
   - feature-server is configured with gold_snapshot_id=<tagged>
   - All historical queries go to that exact snapshot

3. trainer writes artifacts:
   - model.pkl at s3://stock-lake/models/<run_id>/
   - metrics.json
   - decision_log.parquet
   - UPDATE model_training_runs SET status='completed', metrics=<json>, artifact_uri=...

4. Months later, debugging:
   - SELECT * FROM model_training_runs WHERE run_id = X
   - feature-server with that gold_snapshot_id → byte-identical training data
```

---

## 8. Agent learning strategy

### 8.1 Starting conditions

- **Initial capital:** $40,000
- **Position size cap:** 20% of current capital
- **Max concurrent positions:** 1 (single-symbol focus); expand to 3–5 later
- **Lookback:** 20 bars
- **Timeframe:** daily bars first; intraday later
- **Universe:** screener output (~500–1000 symbols on any given day)

### 8.2 Observation space

- 20-bar window of ~50 features (~1000 dims)
- 4-dim position state: (shares_held, unrealized_pnl, bars_in_trade, cash_pct)
- **No bar after `t`. Ever.** Enforced inside feature-server's contract.

### 8.3 Action space

`{0: hold, 1: buy, 2: sell}` — discrete.

- Position sizing is **not** in the action space initially. Fixed rule:
  20% of capital. Smaller action space → faster learning.
- Buy is no-op if already long; sell is no-op if flat.
- Future extension: continuous action with sizing.

### 8.4 Phase 1 — LLM agent (validation harness)

Claude reasons over feature-importance + cluster context, calls MCP
tools, outputs structured `Action`. Used to validate the full pipeline
end-to-end before any RL training.

### 8.5 Phase 2 — RL agent (PPO)

- Stable-Baselines3 PPO.
- Conservative hyperparameters: low LR, tight clip, high gamma (patience).
- Parallel envs across many symbols + time windows.
- Eval callback on held-out symbols every 10k steps.

### 8.6 Phase 3 — Hybrid

LLM picks setups, RL handles entry timing + exits. Combines reasoning
with speed.

---

## 9. Reward engineering

Composite reward, five components, all weights in
`trading_ai/simulation/config.py`.

| Component | Formula | Effect |
|---|---|---|
| R-multiple trade reward | `tanh(realized_pnl / initial_risk)` on close | Bounded [-1, +1]; risk-aware. |
| Transaction cost penalty | `-0.015` per buy/sell action | Friction; punishes churn. |
| Patience reward | `+0.001` when flat and holding | Compounds; patience accrues. |
| Overtrade penalty | `-0.005` per trade beyond 3 in last 20 bars | Direct anti-HFT pressure. |
| Drawdown penalty | `-((dd - 0.10) × 10)²` past 10% | Quadratic; protects $40k. |

```
total_reward =
    trade_reward
  + transaction_cost
  + patience_reward
  + overtrade_penalty
  + drawdown_penalty

clipped to [-3.0, +3.0]
```

### Tunable knobs

| Parameter | Lower → | Higher → |
|---|---|---|
| `transaction_cost` | More trades | Fewer trades |
| `patience_reward` | Normal cadence | Very patient |
| `overtrade_threshold` | More tolerant | Stricter |
| `drawdown_threshold` | Aggressive | Conservative |
| `r_multiple_weight` | Less risk-aware | More risk-aware |

Start with defaults. Train 500k steps. Inspect trade-quality metrics.
Adjust. The agent **will** try to game the reward function; observe
behavior and re-balance weights.

---

## 10. Deployment topology

### 10.1 Local development

One process. All services in-process via Protocol injection. Iceberg
points at the real bucket (or LocalStack).

```bash
poetry run uvicorn app.main_api:app --reload
```

Wired in `app/main_api.py` factory:

```python
feature_server = FeatureServer(iceberg, scaler_path)
screener      = Screener(iceberg)
simulator     = Simulator(reward_engine=RewardEngine(...))
execution     = simulator   # or SchwabClient(mode="paper") in paper mode
agent         = LLMAgent(feature_server, screener, execution, discovery_reader)
```

### 10.2 Production: container per service

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ api-gateway     │  │ feature-server  │  │ execution       │
│ (FastAPI + MCP) │──│ (FastAPI)       │──│ (FastAPI)       │
│ public, scaled  │  │ private, scaled │  │ private,        │
└─────────────────┘  └─────────────────┘  │ singleton       │
        │                    │            │ (per account)   │
        └──────────┬─────────┘            └────────┬────────┘
                   │                               │
                   ▼                               │
         ┌─────────────────┐                       │
         │ agent-runtime   │───────────────────────┘
         │ (long-running)  │
         └─────────────────┘
                   ▲
                   │
         ┌─────────┴─────────┐
         │ evaluator         │
         │ (on-demand worker)│
         └───────────────────┘

[cron jobs]
  discovery-jobs (quarterly)
  screener publish to gold.universes (daily)
```

Each container has its own resource profile (CPU vs GPU, network vs
disk-heavy). One service at a time can be scaled, replaced, or rolled
back without touching others.

### 10.3 Promotion path

```
EXECUTION_BACKEND=sim    → in-process Simulator
EXECUTION_BACKEND=paper  → SchwabClient(mode="paper")
EXECUTION_BACKEND=live   → SchwabClient(mode="live")  [hard capital cap]
```

Same model, same feature-server, same agent code. Only the execution
service's backing implementation changes.

### 10.4 Kill switch

A single boolean in `risk_config.py` (and CH `system_flags` table) that
the execution service polls. Set to `false` → all open orders cancelled,
all new orders rejected. Manual override only.

---

## 11. Build phases

Each phase ends with a green test suite and a measurable gate.

### Phase 1 — feature-server + screener (week 1–2)

**Build:** `trading_ai/feature_server/`, `trading_ai/screener/`.

**Tests:**
- `feature_server.get_features("AAPL", as_of=t, lookback=20)` returns a
  finite normalized vector.
- All ~50 features present; no NaN, no inf.
- Persistence: scaler saves + loads identically.
- Screener returns ~500–1000 symbols on any given trading day.

**Gate:** Features for 100 symbols in < 5s end-to-end. **No future
leakage** verified by a contract test that fails if any returned bar has
`ts > as_of`.

---

### Phase 2 — discovery (week 3)

**Build:** `trading_ai/discovery/`.

**Tests:**
- RF importance returns coherent top-10 (similar features rank
  similarly).
- KMeans clusters have varied forward Sharpe (some good, some bad, some
  neutral).
- Walk-forward validator never lets a future bar into a training fold.

**Gate:** `feature_importance.json` and `cluster_model.pkl` saved at the
documented S3 path. Top-5 features make intuitive sense.

---

### Phase 3 — MCP tools (week 4)

**Build:** `trading_ai/server/mcp_tools/` (extends existing MCP server).

**Tests:**
- Every new tool is callable via curl; returns well-formed JSON.
- Existing MCP tools still work.

**Gate:** Each tool documented in `/mcp/tools` listing; smoke tests
green in CI.

---

### Phase 4 — simulation (week 5)

**Build:** `trading_ai/simulation/` (TradeSimulator, RewardEngine,
CircuitBreaker).

**Tests:**
- Simulator starts at $40,000.
- Buy reduces cash, increases position; sell reverses.
- Slippage applied (buy at ask+slip, sell at bid-slip).
- Reward components produce known values on canned scenarios:
  - +2R closed trade → reward ≈ +0.96
  - -1R closed trade → reward ≈ -0.76
  - holding cash → +0.001
  - 4th trade in 20 bars → -0.005
- Circuit breaker fires at 15% drawdown.

**Gate:** All unit tests pass; canned scenarios match expected rewards.

---

### Phase 5 — LLM agent (week 6–7)

**Build:** `trading_ai/agents/llm_agent.py`,
`trading_ai/agents/decision_logger.py`.

**Tests:**
- LLM agent fetches features + importance via MCP, outputs structured
  `Action`.
- Decision logged with full context (features, reasoning, outcome).
- Runs a full paper-trade loop end-to-end against the simulator.

**Gate:** 100 decisions logged. At least some "hold" actions — agent
isn't just trading constantly.

---

### Phase 6 — RL agent (week 8–10)

**Build:** `trading_ai/agents/rl_env.py`, `trading_ai/agents/rl_agent.py`.

**Tests:**
- Gym env observations have correct shape, no NaN.
- Contract test: env's observation at step `t` never contains bar
  `t+1`.
- PPO training runs 100k steps without crashes.

**Gate (after 500k steps):**
- Avg trades per episode < 5
- Win rate > 50%
- Avg R-multiple > 0.5
- Max drawdown < 20% on training data

---

### Phase 7 — backtest + walk-forward (week 11)

**Build:** `trading_ai/evaluator/`.

**Tests:**
- VectorBT produces same trade list as simulator for identical inputs.
- Walk-forward runs across rolling windows.
- Held-out 6 months confirmed untouched (audit script reads the
  Iceberg snapshot tag for the training run, asserts the held-out date
  range was never included).

**Gate (out-of-sample):**
- Sharpe > 1.0
- Win rate > 55%
- Profit factor > 1.5
- Max drawdown < 15%
- ≥ 200 trades in test period

---

### Phase 8 — Schwab paper validation (week 12–13)

**Build:** `trading_ai/execution/schwab_client.py`,
`trading_ai/monitoring/divergence_tracker.py`.

**Tests:**
- Schwab paper auth works.
- Paper orders return real fill confirmations.
- Divergence tracker computes sim-vs-paper deltas.

**Gate to live (2 weeks paper):**
- Sim/paper return divergence < 30%
- No catastrophic surprise (paper drawdown not >> sim drawdown)
- Trade frequency matches sim within 20%

---

### Phase 9 — live (conditional)

Only after Phase 8 gates pass. Start with **$5–10k of the $40k
allocation**. Monitor one week. Scale up only if behavior matches paper.

---

## 12. Validation gates

| From → To | Criteria |
|---|---|
| 1 → 2 | Features for 100 symbols in < 5s; no-future-leakage test green |
| 2 → 3 | Top-5 features ranked + cluster_model.pkl saved |
| 3 → 4 | All MCP tools return valid JSON; existing tools regression-free |
| 4 → 5 | Simulator + reward unit tests green |
| 5 → 6 | 100 LLM decisions logged with outcomes |
| 6 → 7 | RL: trade freq <5/ep, win rate >50%, R-mult >0.5, MDD <20% |
| 7 → 8 | OOS Sharpe >1.0, WR >55%, PF >1.5, MDD <15% |
| 8 → 9 | 2 weeks paper, sim/paper divergence <30% |

---

## 13. Risk controls

### Code-level (enforced in services, not callers)

- **Hard position cap.** 20% of capital. Enforced in both simulator and
  Schwab client — cannot be set higher via config.
- **Drawdown circuit breaker.** Auto-halt at 15%. Cannot be disabled by
  agent. Lives in execution service.
- **Max trades/day.** Configurable, default 10. Execution rejects beyond.
- **Max concurrent positions.** Starts at 1.
- **Symbol whitelist.** Only symbols from today's screener output are
  tradeable. Updated daily; cached in execution.

### Process-level

- **Held-out test set.** Last 6 months never touched in training.
  Enforced by audit script reading the training run's pinned Iceberg
  snapshot and asserting the snapshot's `silver.ohlcv_1m` doesn't include
  bars in the held-out window.
- **Walk-forward required.** No promotion to paper without passing
  walk-forward.
- **Divergence gate.** Promotion to live blocked if sim/paper divergence
  > 30%.
- **Reduced live capital.** First 2 weeks of live use $5–10k, not full
  $40k.
- **Daily trade audit.** All decisions logged with full context.

### Pre-live checklist

- [ ] Held-out test set untouched (audit script green)
- [ ] Walk-forward Sharpe > 1.0 on OOS
- [ ] Slippage + commission modeled
- [ ] Position cap enforced in both simulator and Schwab client
- [ ] Circuit breaker tested at 10/15/20% thresholds
- [ ] Sim/paper divergence < 30% over 2 weeks
- [ ] No look-ahead bias (contract test passing)
- [ ] Earnings dates excluded by screener
- [ ] Decisions logged in structured format
- [ ] Reward clipped to prevent gradient explosion
- [ ] Drawdown alerts wired
- [ ] Capital limits hard-coded in execution service
- [ ] Kill switch tested

---

## 14. Tech stack

| Layer | Tool | License | Why |
|---|---|---|---|
| Data lake | Iceberg + S3 + Glue | Apache 2.0 | Existing platform. |
| Hot store | ClickHouse | Apache 2.0 | Existing platform. |
| Feature compute | pandas + custom | — | Already in `app/indicators/`. |
| Normalization | scikit-learn RobustScaler | BSD-3 | Fat-tail safe. |
| Discovery | scikit-learn (RF, KMeans) | BSD-3 | Interpretable. |
| Backtest | vectorbt OSS | Apache 2.0 | Vectorized; fast. |
| RL framework | stable-baselines3 | MIT | Production-grade PPO. |
| RL env | gymnasium | MIT | Standard interface. |
| LLM | Anthropic SDK | API | Reasoning + MCP native. |
| API | FastAPI | MIT | Existing. |
| Broker | Schwab Trader API | Commercial | Paper → live, same code path. |
| Experiment tracking | TensorBoard | Apache 2.0 | Built into SB3. |
| Container orchestration | Docker Compose (dev), ECS/K8s (prod) | — | Service-per-container. |

---

## Final notes for implementers

1. **Build feature-server first; nothing else is testable without it.**
   Treat the no-future-leakage contract test as a hard gate.
2. **Don't refactor `app/services/` to follow the new template all at
   once.** Add new services under `trading_ai/` using the template; let
   the older modules migrate when touched.
3. **Use Pydantic for every boundary.** Hand-rolled dicts at boundaries
   are the #1 source of silent schema drift.
4. **The simulator is the foundation.** Spend extra time on slippage and
   commission; everything downstream stands on it.
5. **Pin every training run to Iceberg snapshots.** Free at our scale;
   irrecoverable if you skip it.
6. **The agent will game the reward.** When you see degenerate behavior
   (never trades, or always trades), rebalance reward weights. Expected.
7. **Walk-forward is sacred.** It's the only thing between you and an
   overfit model.
8. **Last 6 months of data: untouched. Period.**
9. **Treat $40k as real, even in sim.** Position sizing, drawdown,
   circuit breaker — all wired from day one.
10. **Test the circuit breaker manually before training starts.** Force a
    15% drawdown scenario and confirm the simulator halts.

**Total estimated time:** 10–14 weeks to live trading with all gates
passed. Phase 6 (RL training + reward tuning) is where timelines slip.
