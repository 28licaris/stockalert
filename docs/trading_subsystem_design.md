# Trading Subsystem Design

Concrete implementation design for the trading AI subsystem — the
backtest harness, strategy framework, portfolio accounting, evaluator,
and run registry that all agents (LLM, RL, rule-based) plug into.

This document complements [`trading-ai-build-plan.md`](trading-ai-build-plan.md).
That doc is the **strategic roadmap** (services + phases + reward
engineering). This doc is the **implementation contract** — the
Pydantic shapes, Protocols, folder layout, and modularity guarantees
that govern the code we're about to write.

When the plan and this doc conflict, **code wins**
(per [`standards/doc_discipline.md`](standards/doc_discipline.md)).

---

## 1. System intent

We are building a **modular, swappable, reproducible trading research
platform** so we can:

1. Backtest swing-trading strategies on historical bronze bars,
   measure their edge, and improve them.
2. Plug in different timeframe granularities (`1d` first, then `1h`,
   `30m`, `15m`, `5m`, `1m`) WITHOUT rewriting strategies — the same
   `Strategy` class runs against any interval.
3. Plug in different strategies (rule-based, LLM-driven, RL-trained)
   WITHOUT rewriting the backtester — every strategy implements the
   same `Strategy` Protocol.
4. Plug in different **indicator transforms** (SMA, EMA, RSI, MACD,
   Bollinger, ATR, …) WITHOUT touching the strategy code — strategies
   request indicators by name from the context object.
5. Pin every backtest run to an immutable Iceberg snapshot so a
   result from six months ago is bit-for-bit reproducible today.
6. Move smoothly from research → paper trading → live, with the same
   `Strategy` running in all three modes (only the executor changes).

**Scope sequence:** start with swing trading on daily bars. The
abstractions are designed so day-trading on 1-minute bars is a
configuration change, not a rewrite.

---

## 2. Design principles

Beyond [`standards/platform_design.md`](standards/platform_design.md):

### 2.1 Strategy state is local; harness state is global

A strategy owns its internal state (indicator buffers, position
intent, signal cooldowns). The harness owns portfolio state (cash,
positions, trades). The two communicate only via:

- Harness → Strategy: a `Context` object describing the current bar +
  history window + portfolio snapshot + indicator API.
- Strategy → Harness: an `Action` describing what to do next bar.

No globals. No cross-call mutation through anything other than the
context. Lets us **deterministic-replay** any strategy run.

### 2.2 Bars in, actions out — every layer

The fundamental unit is a `Bar`. Backtester pumps bars; strategies
emit actions; portfolio executes actions; evaluator measures
outcomes. No bypass paths. A different timeframe means a different
bar source, not a different harness.

### 2.3 Indicators are pure functions of price series

Already established in
[`app/indicators/README.md`](../app/indicators/README.md). Strategies
ask the context for `ctx.indicator("sma_20")` which lazily computes
and caches. No mutable shared indicator state across strategies.

### 2.4 Reproducibility is non-negotiable

Every backtest run records:

- Iceberg snapshot ID of the bronze table read at run time
- Strategy class name + version + serialized params
- Date range + universe + interval + fees model
- Result metrics
- Run UUID + run timestamp

Stored in `agent_runs` table in ClickHouse. Re-running with the same
inputs produces the same metrics — verified by a regression test.

### 2.5 Configuration is data, not code

Strategy parameters live in Pydantic models. Backtest runs are
described by serializable `BacktestConfig` objects. An agent can ask
"what was the config of run X?" and get a complete answer; an
operator can hand-edit a YAML config and re-run.

### 2.6 Cost-aware by construction

For LLM-driven strategies: every model call must be cacheable on
`(symbol, ts, context_hash)` so a replay doesn't re-pay the
API cost. The strategy interface enforces this — `on_bar` is
expected to be deterministic given the same context, which makes
caching trivial.

For backtests: prefer cheap intervals during research (daily ≪ 1min).
The interval is a config, so research can iterate on daily and
graduate to 1min later for the same strategy.

---

## 3. Core abstractions

### 3.1 Bar

Reuses [`LiveBar`](../app/services/readers/schemas.py) and
[`BronzeBar`](../app/services/readers/schemas.py) from the existing
schemas. The backtest engine treats them uniformly via a thin
`Bar` Protocol — anything with `symbol / timestamp / open / high /
low / close / volume` qualifies.

No new schema; the data layer's contracts already cover this.

### 3.2 Indicator

Existing [`Indicator(ABC)`](../app/indicators/base.py). Strategies
access indicators via the context, not by instantiating them
directly:

```python
class MyStrategy(Strategy):
    def on_bar(self, ctx: Context) -> Action:
        sma_20 = ctx.indicator("sma", period=20)   # cached
        sma_50 = ctx.indicator("sma", period=50)
        ...
```

The context owns indicator caching keyed on `(name, **params)`. The
INDICATOR_REGISTRY maps name → class:

```python
INDICATOR_REGISTRY = {
    "sma": SMA, "ema": EMA, "wma": WMA,
    "rsi": RSI, "macd": MACD, "tsi": TSI,
    "atr": ATR, "bollinger": BollingerBands, ...
}
```

Adding a new indicator = subclass `Indicator`, add one line to the
registry, done. **Existing strategies don't change.**

### 3.3 Strategy (Protocol)

```python
from typing import Protocol

class Strategy(Protocol):
    name: str           # serializable identifier
    version: str        # bumped on logic change → invalidates result caches
    interval: str       # required bar interval ('1d', '1h', '5m', '1m', ...)

    def setup(self, ctx: Context) -> None: ...
    def on_bar(self, ctx: Context) -> Action: ...
    def teardown(self, ctx: Context) -> None: ...
```

Lifecycle:
- `setup(ctx)` — once before the run. Allocates strategy-local state.
- `on_bar(ctx)` — once per bar. Returns one Action.
- `teardown(ctx)` — once after the run. Releases resources (e.g. LLM
  HTTP session).

All three are optional; sensible defaults from `BaseStrategy`. Every
concrete strategy has a Pydantic `Params` model and reads its config
from there.

### 3.4 Context

```python
class Context:
    """Per-bar view passed to Strategy.on_bar."""
    bar: Bar                       # current bar
    history: BarHistory            # last N bars (deque-backed)
    portfolio: PortfolioSnapshot   # cash, positions, equity (read-only)
    clock: datetime                # current bar's timestamp (UTC)
    config: BacktestConfig         # the full run config, read-only

    def indicator(self, name: str, **params) -> pd.Series:
        """Lazy-compute + cache. Same call returns same series within a run."""

    def log(self, **fields) -> None:
        """Structured per-bar logging — captured into the RunResult."""
```

The context is **read-only from the strategy's perspective**. The
strategy emits an Action; the harness mutates the portfolio. This
makes strategies trivially testable in isolation.

### 3.5 Action

```python
class Action(BaseModel):
    kind: Literal["hold", "buy", "sell", "set_position"]
    symbol: str
    size: float = 0.0      # shares (or fractional). Meaning depends on `kind`.
    limit_price: float | None = None
    stop_price: float | None = None
    note: str = ""         # optional human/agent reason
```

`set_position` is a target-quantity action — useful for portfolio
strategies that say "I want 100 shares of AAPL right now," letting
the harness compute the delta.

### 3.6 Portfolio

```python
class Position(BaseModel):
    symbol: str
    quantity: float
    avg_entry_price: float
    entry_time: datetime
    unrealized_pnl: float

class Portfolio:
    cash: float
    positions: dict[str, Position]
    equity_curve: list[tuple[datetime, float]]    # mark-to-market per bar
    closed_trades: list[Trade]
```

Marks-to-market on every bar via the bar's close. Records the equity
curve so the evaluator can compute Sharpe / drawdown post-hoc.

### 3.7 Fees + slippage (pluggable)

```python
class FeeModel(Protocol):
    def fee_for(self, action: Action, fill_price: float) -> float: ...

class SlippageModel(Protocol):
    def fill_price(self, action: Action, bar: Bar) -> float: ...
```

Defaults: `ZeroFees`, `NextBarOpenFill` (simple, conservative).
Realistic defaults: `PerShareFees(0.005, min_commission=1.00)`,
`PercentSlippage(0.0005)`. Configurable per run.

**Live sim-trade surface (FE-CONTRACTS-5, locked 2026-05-18).**
The cockpit's live paper-trading endpoints (`POST /api/v1/sim/trades`,
etc. — full schema in
[frontend_api_contracts.md §4.6](frontend_api_contracts.md)) reuse
these **same** Protocols. The operator picks one active `FeeModel`
and one active `SlippageModel` globally (`GET/PUT /api/v1/sim/cost-config`);
every live sim trade fills through them and persists the audit
trail (`requested_price`, `fill_price`, `slippage_amount`,
`slippage_bps`, `fees`, `fees_model_name`, `slippage_model_name`) in
the `sim_trades` ClickHouse table. This makes backtest results and
live sim performance directly comparable — same cost model, same
math. Future realism work (spread-aware, volume-aware, partial-fill
models) is purely additive: new class implementing the Protocol +
registry entry, zero schema change.

### 3.8 Backtester

```python
class Backtester:
    def __init__(
        self, *,
        bar_source: BarSource,        # BronzeReader or BarReader, abstracted
        fees: FeeModel,
        slippage: SlippageModel,
        starting_cash: float = 40_000.0,
    ): ...

    def run(self, strategy: Strategy, config: BacktestConfig) -> RunResult: ...
```

Pseudocode:

```
1. snapshot_id = lake.current_snapshot_id(bronze_table_for(interval))
2. bars = bar_source.get_bars(symbols, start, end, interval)
3. ctx = Context(history=BarHistory(maxlen=config.history_window))
4. strategy.setup(ctx)
5. for bar in bars:
      ctx.advance(bar, portfolio.snapshot())
      action = strategy.on_bar(ctx)
      portfolio.apply(action, bar, fees, slippage)
6. strategy.teardown(ctx)
7. return RunResult(metrics=evaluator.compute(portfolio), snapshot_id, ...)
```

### 3.9 RunResult + RunMetrics

```python
class RunMetrics(BaseModel):
    total_return: float            # final_equity / starting_cash - 1
    annualized_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float            # negative number, lower is worse
    win_rate: float                # n_winning / n_trades
    profit_factor: float           # sum(wins) / abs(sum(losses))
    n_trades: int
    avg_trade_pnl: float
    avg_winner_pnl: float
    avg_loser_pnl: float
    longest_drawdown_days: int

class RunResult(BaseModel):
    run_id: UUID
    started_at: datetime
    finished_at: datetime
    strategy_name: str
    strategy_version: str
    strategy_params: dict          # serialized Params model
    config: BacktestConfig
    snapshot_id: str               # Iceberg snapshot pinned at start of run
    metrics: RunMetrics
    equity_curve: list[tuple[datetime, float]]
    trades: list[Trade]            # in full result; trimmed on registry write
```

### 3.10 Evaluator (pluggable)

```python
class Evaluator(Protocol):
    def compute(self, portfolio: Portfolio, config: BacktestConfig) -> RunMetrics: ...
```

Default: `StandardEvaluator` produces the canonical metric set above.
Strategies that need custom metrics ship their own evaluator.

### 3.11 AgentRun registry

ClickHouse table `agent_runs`:

```sql
CREATE TABLE agent_runs (
    run_id              UUID,
    started_at          DateTime64(3, 'UTC'),
    finished_at         DateTime64(3, 'UTC'),
    strategy_name       LowCardinality(String),
    strategy_version    String,
    strategy_params     String,            -- JSON
    config              String,            -- JSON
    snapshot_id         String,            -- Iceberg snapshot pinned at run start
    symbols             Array(String),
    interval            LowCardinality(String),
    start_date          Date,
    end_date            Date,
    total_return        Float64,
    sharpe_ratio        Float64,
    max_drawdown        Float64,
    win_rate            Float64,
    profit_factor       Float64,
    n_trades            UInt32,
    metrics_full        String,            -- JSON: full RunMetrics
    git_sha             String DEFAULT ''  -- code version when run executed
)
ENGINE = MergeTree
ORDER BY (started_at, strategy_name)
PARTITION BY toYYYYMM(started_at);
```

Trades + equity curve archived separately to S3/MinIO when they
exceed an inline-size budget (keep CH rows cheap).

---

## 4. Folder layout

```
app/
├── indicators/                  pure TA math (existing; gets expanded)
│   ├── base.py
│   ├── rsi.py / macd.py / tsi.py  (existing)
│   ├── sma.py / ema.py / wma.py   (Phase TA-1)
│   ├── atr.py / bollinger.py      (TA-1 if a strategy needs them)
│   ├── registry.py                INDICATOR_REGISTRY name → class
│   └── README.md
│
├── signals/                     pattern detectors (existing)
│   ├── divergence.py
│   └── README.md
│
└── services/
    ├── readers/                 (existing — backtest bar source)
    │   └── ...
    │
    └── sim/                     NEW — the trading subsystem
        ├── schemas.py               Action, Position, Trade, RunMetrics,
        │                            RunResult, BacktestConfig
        ├── strategy.py              Strategy Protocol, Context, BaseStrategy
        ├── context.py               Context implementation + BarHistory
        ├── portfolio.py             Portfolio + Position accounting
        ├── fees.py                  FeeModel / SlippageModel + defaults
        ├── backtester.py            Backtester.run()
        ├── evaluator.py             StandardEvaluator + Evaluator Protocol
        ├── registry.py              agent_runs CH writer / reader
        ├── strategies/              concrete strategies — pluggable
        │   ├── sma_crossover.py       (TA-1 canary)
        │   ├── rsi_threshold.py       (TA-1 second baseline)
        │   └── llm_agent.py           (TA-2)
        ├── tests/                   unit + integration tests
        └── README.md
```

Module rules (per [`standards/service_modules.md`](standards/service_modules.md)):

- Cross-service imports come from `schemas.py` (or `strategy.py` for
  the Protocol). Never from `backtester.py` / `portfolio.py`.
- `Strategy` is a Protocol, not an ABC — duck-typing is fine,
  inheritance from `BaseStrategy` is convenience.
- `from_settings()` for any service touching `app.config`.
- No CH or HTTP imports in `strategy.py` or `strategies/*.py` —
  strategies are pure (price + indicators → action). Side effects
  (logging, model API calls) flow through the `Context`.

---

## 5. Data flow — one backtest run

```
1. Operator (or agent) calls: backtester.run(strategy, config)

2. Backtester resolves the bar source:
     - config.interval in {'1d', '1h', '30m', '15m', '5m', '1m'}
     - For historical date ranges: BronzeReader.get_bars(...)
       (covers >T+1 data; bronze is canonical for ML)
     - For very recent (today's session): BarReader.get_recent_bars / 
       get_bars_in_range (CH live tier; T+0 freshness)
     - The selection is automatic based on (start, end) vs
       latest_trading_day(provider).

3. Backtester captures the Iceberg snapshot_id at run start:
     - snapshot_id = bronze_table_for(interval).current_snapshot().snapshot_id
     - Pinned in RunResult; same input → same data → same output forever.

4. Backtester instantiates Context with empty BarHistory(maxlen=N).

5. strategy.setup(ctx)
     - Strategy allocates any state, registers indicators it'll use
       (warm-up gets handled by Context's lazy indicator API).

6. for each bar in bars:
     a. ctx.advance(bar, portfolio.snapshot())
        - Appends to history. Stamps clock.
     b. action = strategy.on_bar(ctx)
        - Pure: same ctx → same action.
     c. portfolio.apply(action, next_bar.open, fees, slippage)
        - Fills happen on the NEXT bar's open by default (avoid
          look-ahead bias). Pluggable via SlippageModel.
        - Mark-to-market this bar's close → equity_curve.

7. strategy.teardown(ctx)

8. metrics = evaluator.compute(portfolio, config)

9. registry.write(RunResult(...))
     - Insert into agent_runs.
     - If trades/equity exceed inline budget, archive to S3.

10. Return RunResult.
```

---

## 6. Modularity contracts (explicit)

These are the things that MUST stay swappable. Tests enforce them.

### 6.1 Pluggable timeframes
- Swap `config.interval` from `"1d"` to `"5m"` and the same strategy
  runs. No strategy code changes.
- Multi-timeframe strategies declare `intervals = ["1d", "1h"]` and
  receive a Context that resolves the right history slice per call.

### 6.2 Pluggable strategies
- Implement `Strategy` Protocol — that's it.
- LLM-driven, RL-driven, rule-based all coexist in
  `app/services/sim/strategies/`.
- A registry-style strategy loader: `load_strategy("sma_crossover", params)`
  returns a configured instance.

### 6.3 Pluggable indicators
- Subclass `Indicator(ABC)`, add to `INDICATOR_REGISTRY`. Strategies
  reference by name, never by class.
- New indicator + new strategy using it = two files added, zero
  existing files touched.

### 6.4 Pluggable fees / slippage / evaluator
- Each is a Protocol. Defaults provided. Operator (or agent) can pass
  a different implementation per run.

### 6.5 Pluggable bar source
- BronzeReader for historical, BarReader for live — both already
  exist. Backtester accepts a `BarSource` Protocol; future Silver /
  Gold layers plug in via the same Protocol.

---

## 7. Reproducibility contract

Every `RunResult` carries enough state to **bit-for-bit re-run** the
backtest:

| Pin | Source |
|---|---|
| Code version | `git_sha` captured at run-time (`subprocess.run(["git", "rev-parse", "HEAD"])`) |
| Data version | `snapshot_id` from Iceberg's current snapshot at run start |
| Strategy version | `strategy.version` field (manually bumped on logic change) |
| Strategy params | Serialized Pydantic `Params` model |
| Run config | Serialized `BacktestConfig` (symbols, dates, interval, fees, slippage, starting_cash) |

A `reproduce(run_id)` CLI: load the row from `agent_runs`, checkout
the pinned git_sha (warn if working tree differs), instantiate
strategy + config from JSON, run, diff metrics. Identical = pass.

---

## 8. Configuration

```python
class BacktestConfig(BaseModel):
    symbols: list[str]
    start: datetime
    end: datetime
    interval: Literal["1d", "1h", "30m", "15m", "5m", "1m"]
    starting_cash: float = 40_000.0
    history_window: int = 200      # max bars in Context.history
    fees_model: Literal["zero", "per_share", "percent"] = "per_share"
    fees_params: dict = Field(default_factory=dict)
    slippage_model: Literal["next_bar_open", "percent"] = "next_bar_open"
    slippage_params: dict = Field(default_factory=dict)
    provider: Literal["polygon", "schwab"] = "polygon"
```

Strategies declare their own Params:

```python
class SmaCrossoverParams(BaseModel):
    fast_period: int = 20
    slow_period: int = 50
    position_size_pct: float = 0.95   # fraction of cash deployed
```

Loading a backtest from YAML/JSON:

```yaml
strategy: sma_crossover
strategy_params:
  fast_period: 10
  slow_period: 30
config:
  symbols: [AAPL, MSFT, NVDA, SPY, QQQ]
  start: 2023-01-01
  end: 2024-12-31
  interval: 1d
  starting_cash: 40000
```

---

## 9. Testing strategy

### 9.1 Unit
- Each strategy: synthetic bar stream → assert specific actions emit
  at expected bars. Strategies are pure → trivial to test.
- Each indicator: known input series → known output series with
  known tolerances.
- Portfolio: every Action kind, every edge case (insufficient cash,
  short position, fractional shares).
- Fees / slippage models: each scenario.

### 9.2 Integration
- Real bronze + real strategy + small universe + short window → run
  completes, RunResult has plausible metrics.
- `agent_runs` row written and reloadable.
- Reproducibility: same inputs → same metrics.

### 9.3 Regression (structural gates)
- `test_strategy_is_pure`: strategies don't import `app.db.*`,
  `app.providers.*`, `requests`, or anything network-y. Strategy
  code must depend only on `app.indicators.*`, `app.services.sim.schemas`,
  `app.services.sim.strategy`. (Side-effecting strategies — LLM,
  external feature server — go in dedicated subfolder with explicit
  side-effect annotation.)
- `test_backtester_is_deterministic`: same bars + same strategy +
  same seed → same metrics.

---

## 10. Phasing

### Phase TA-1: Core harness + canary strategy (next session)
- `app/services/sim/` scaffold (schemas, strategy Protocol, context,
  portfolio, fees, backtester, evaluator, registry).
- `app/indicators/` expanded with SMA + EMA.
- One canary strategy: `SmaCrossoverStrategy` (proves the harness,
  not for production trading).
- `agent_runs` CH table.
- Integration test: 1 year of AAPL daily bronze → metrics row written.
- CLI: `python scripts/run_backtest.py --config configs/canary.yaml`

**Gate:** running the canary against real production bronze produces
a row in `agent_runs` with a non-trivial equity curve. Re-running
the same config produces an identical metrics row (reproducibility
test).

### Phase TA-2: LLM-driven strategy (after TA-1)
- `app/services/sim/strategies/llm_agent.py` — wraps Claude (via
  Anthropic SDK) with cache-on-context-hash.
- New MCP tool `run_backtest(strategy, config)` so an agent can
  self-evaluate.
- Cost-budgeted: backtest with N bars caps LLM calls at ≤N, caches
  on `(symbol, ts, ctx_hash)`.

### Phase TA-3: More indicators + more strategies (parallel)
- Indicators: ATR, Bollinger, Stochastic, additional MAs.
- Strategies: RSI-extreme reversion, ATR-breakout, mean-revert pair.

### Phase TA-4.1: Multi-timeframe foundation (LANDED 2026-05-17)
- Strategy declares `intervals: list[str]` (coarsest-to-finest);
  Context exposes one BarHistory per interval via
  `history_at(interval)` and indicators by interval via
  `indicator(name, interval=..., **params)`. Single-TF
  strategies that declare only `interval: str` continue to
  work unchanged via the `required_intervals(strategy)` helper.
- Backtester iterates the EXECUTION interval (the finest one);
  coarser intervals are released to Context only when
  `coarser_bar.timestamp + interval_duration <= execution_bar.timestamp`.
  This is the **no-look-ahead invariant**, regression-tested.
- `BacktestConfig.intervals: list[str] | None` for operator
  override; if set, takes precedence over the strategy's
  declared intervals.
- Bar fetch resolves per-interval: '1m' → BronzeReader (snapshot
  pinned), all other intervals → BarReader (CH).

### Phase TA-4.2: First multi-TF strategy (LANDED 2026-05-17)
- `MtfEmaTrendFiltered` — daily SMA(50) trend filter gates
  hourly EMA(12)/EMA(26) crossover entries. First strategy to
  exercise the multi-TF Context.
- Live run: AAPL Jun-Dec 2024 hourly, 44 trades, -9.75% return,
  Sharpe -1.168. MTF infrastructure verified; strategy noisy —
  filed for parameter tuning + a counter-strategy in TA-5+.

### Phase TA-4.3: Screener service (LANDED 2026-05-17)

Closes the canonical swing-trade pipeline:

    universe (1000s) → screener → 10-30 candidates → strategy

- `app/services/screener/` — `ScreenerSpec` (declarative Pydantic;
  no eval/DSL, agent-safe) + `Screener.scan(spec) → ScreenerResult`.
  13 rule kinds (`close_above_sma`, `rsi_below`,
  `close_at_lower_band`, `atr_pct_above`, `price_above`, …) over
  one OHLCV DataFrame per symbol. Rules compose via logical AND.
- Universe sources: explicit symbol list, watchlist name, or
  union of both. Mixed-case input is uppercased + deduped.
- Bar source mirrors backtester/IndicatorReader: `interval='1m'`
  → `BronzeReader` (Iceberg snapshot pinned in
  `ScreenerResult.snapshot_id`); everything else → `BarReader`
  (CH live tier).
- Ranking: `volume` / `atr_pct` / `rsi` (ascending — most oversold
  first) / `rsi_desc` / `none` (universe order). Capped by
  `spec.limit`.
- Surfaces: `POST /api/screener/scan` HTTP route and
  `scan_universe` MCP tool — both consume `ScreenerSpec` and
  return `ScreenerResult` (one contract, two surfaces).
- Per-symbol fetch / indicator failures land in
  `ScreenerResult.errors` so a partial-failure universe still
  returns useful candidates. Spec-author errors (unknown rule
  kind, missing param) raise `ValueError` → HTTP 400.

This is the per-cycle "what to look at" stage. Higher-cost
strategies (LLM, RL) run only on the screener's short-list, not
the full universe.

### Phase TA-5: Silver layer (data foundation)

**Detailed plan:** [silver_layer_plan.md](silver_layer_plan.md).

- TA-5.0 Corp-actions ingestion → `silver.corp_actions`
- TA-5.1 Silver build job → `silver.ohlcv_1m` + `silver.bar_quality`
- TA-5.2 `SilverReader` + flip backtester reads from bronze to silver
- TA-5.3 `silver_to_ch_backfill` mode (the cockpit warming-up unlock)
- TA-5.4 Shadow validation + flip-the-default
- TA-5.5 Retire provider-REST backfills

### Phase TA-R: Risk management (in parallel with TA-5)

**Detailed plan:** [risk_management_plan.md](risk_management_plan.md).
Added per [SYSTEM_REVIEW_2026-05-17.md §3 #1](SYSTEM_REVIEW_2026-05-17.md).
**This is a prerequisite for any paper or live execution.**

- TA-R.1 Risk Manager scaffold + state persistence
- TA-R.2 The 8 rules + composition (kill-switch, daily-loss halt,
  max-DD halt, max-position-size, max-concentration, max-leverage,
  ATR-volatility sizing, cooldown)
- TA-R.3 Backtester integration (`BacktestConfig.risk_policy`)
- TA-R.4 Adversarial tests
- TA-R.5 HTTP + MCP surfaces
- TA-R.6 Decision log + observability

### Phase TA-6: Indicator gap-fill

Per [SYSTEM_REVIEW_2026-05-17.md](SYSTEM_REVIEW_2026-05-17.md) and the
backlog in [`app/signals/README.md`](../app/signals/README.md):

- Volume indicators (OBV, MFI, VWAP-anchored, VWAP bands)
- Volatility / channels (Donchian, Keltner)
- Trend strength (ADX, CCI, ROC)
- Re-introduce divergence detection as a registered indicator
- Optional: pivot detection promoted to `app/indicators/pivots.py`
  (foundation for EW-1)

### Phase TA-7: Gold features tier

**Detailed plan:** [data_platform_plan.md §7](data_platform_plan.md).

- `gold.features_daily` (per `(symbol, date)` — pre-computed
  indicators + microstructure features)
- `gold.features_1m` (intraday equivalent)
- Snapshot-pinned, rebuildable from silver.

### Phase TA-8: Universe history (promoted from later)

Per [SYSTEM_REVIEW_2026-05-17.md §3 #2](SYSTEM_REVIEW_2026-05-17.md).
**This was originally a much-later phase; the System Review
promotes it because every backtest is currently survivorship-biased
without it.**

- `gold.universes` table (S&P 500 / Russell 1000 / Russell 3000
  membership over time).
- Ingest from Wikipedia historical S&P 500 deltas (free) or
  Sharadar (if budget allows).
- Backtest harness automatically filters strategy universe to
  the historical universe-as-of-bar-timestamp. Cures survivorship
  bias.

### Phase TA-9: OOS bake-off harness

Per [SYSTEM_REVIEW_2026-05-17.md §3 #3](SYSTEM_REVIEW_2026-05-17.md).
**Current strategy results are single-symbol single-window.
No evidence of real edge. Required gate before any execution.**

- `scripts/run_oos_bakeoff.py` — runs each strategy across ≥5
  symbols × ≥3 disjoint years.
- Walk-forward training/validation split for any tuned params.
- Acceptance threshold for promotion to paper trading: OOS Sharpe > 1.0
  on at least 3 of the 5 symbols.

### Phase TA-10: Regime classifier

Per [SYSTEM_REVIEW_2026-05-17.md §3 #4](SYSTEM_REVIEW_2026-05-17.md).
**Today's strategies are regime-agnostic; bake-off proves this is
fragile (EMA-cross wins in trend, Bollinger-revert wins in range,
neither survives the other regime).**

- `app/services/regime/` module.
- Baseline classifier: ADX-based + volatility-quantile.
- Strategies declare which regimes they work in (`@only_in('trend_up')`).
- Future: HMM / RL-style state classification.

### Phase TA-11: Execution layer

Per [SYSTEM_REVIEW_2026-05-17.md §3 #6](SYSTEM_REVIEW_2026-05-17.md).
**Detailed plan to be written: `docs/execution_plan.md`.**

- `Executor` Protocol (already-implicit; formalize it).
- `BacktestExecutor` (wraps Portfolio — existing).
- `PaperExecutor` (Schwab paper account; same API).
- `OrderManagementSystem` (child orders, partials, time-in-force).
- `ReconciliationJob` (every 5 min: broker positions vs internal).
- `LiveExecutor` (gated by hard config flag + risk-policy
  requirements).

### Phase TA-12: Live observability infra

Per [SYSTEM_REVIEW_2026-05-17.md §3 #7](SYSTEM_REVIEW_2026-05-17.md).
**Detailed addendum:** extend
[STARTUP_FLOW.md](STARTUP_FLOW.md).

- Prometheus metrics (per-CH-insert counters, per-tick latency
  histograms, per-monitor gauges).
- Sentry exception capture.
- Loki structured log aggregation.
- Pager integration (PagerDuty or Twilio SMS).
- Daily "morning brief" email/Slack (yesterday's P&L, current
  positions, halt-conditions tripped, data freshness).

### Phase TA-RL: RL agent (Trading-AI plan Phase 2)

**Depends on all prior phases.** PPO trained against the backtest
harness as its environment. Reward = stepped Sharpe contribution.
Same `Strategy` Protocol — RL agent IS a strategy. EW state +
regime context + gold features all feed the RL state vector.

### Phase TA-Live: Paper → live trading

**Depends on TA-R (risk) + TA-9 (OOS evidence) + TA-11
(execution) + TA-12 (observability) — all required.**

- Trading-AI plan Phases 8 + 9.
- Same Strategy class; LiveExecutor instead of PaperExecutor.
- Mandatory: kill-switch tested in production; risk policy
  active; reconciliation green for 30+ days of paper trading
  with realistic capital; full observability operational.

---

## 11. Decision log

- **2026-05-16** — Swing first, day later. Swing on daily bars gets
  us a working loop in days. Day trading on 1-min bars is the same
  abstractions with a different `interval` config.
- **2026-05-16** — `Strategy` is a Protocol, not an ABC. LLM-agent
  strategies don't naturally inherit from a Python class; duck-typing
  on `on_bar` is enough. `BaseStrategy` provides convenience defaults
  for setup/teardown for the common rule-based case.
- **2026-05-16** — Fills happen on next bar's open by default. Avoids
  look-ahead bias (filling on the same bar's close lets a strategy
  "see" the close before deciding). Configurable for strategies that
  need finer control (e.g. market-on-open / market-on-close orders).
- **2026-05-16** — Starting cash defaults to $40k (per trading-ai
  plan). Parameterized per run.
- **2026-05-16** — Bronze is the canonical data source for backtests.
  Live tier (CH) is fallback for very-recent-today windows that
  bronze hasn't ingested yet.
- **2026-05-16** — Reproducibility via Iceberg snapshot_id pinning,
  not by copying data. The same snapshot ID resolves to the same
  files forever (immutable Iceberg semantics).
