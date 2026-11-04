# sim/

Trading subsystem — backtest harness, strategy framework, portfolio
accounting, evaluator, and run registry that all agents (LLM, RL,
rule-based) plug into.

**Implementation contract:**
[docs/trading/subsystem_design.md](../../../docs/trading/subsystem_design.md).
Read it before changing anything here.

**Strategic roadmap:**
[docs/trading/roadmap.md](../../../docs/trading/roadmap.md).

## Layout

| File | Owns |
|---|---|
| [schemas.py](schemas.py) | `Action`, `Position`, `Trade`, `RunMetrics`, `RunResult`, `BacktestConfig`, `Bar` Protocol, `PortfolioSnapshot` |
| [strategy.py](strategy.py) | `Strategy` Protocol + `BaseStrategy` convenience class |
| [context.py](context.py) | `Context` (per-bar view passed to strategies) + `BarHistory` |
| [portfolio.py](portfolio.py) | `Portfolio` — cash, positions, equity curve, trade log |
| [risk.py](risk.py) | `RiskManager` — portfolio-level gating: max concurrent positions + portfolio heat |
| [ranker.py](ranker.py) | Cross-sectional ranking for dynamic-universe selection |
| [filters.py](filters.py) | Entry/exit filters |
| [market_context.py](market_context.py) | Regime / market-wide context for strategies |
| [intraday.py](intraday.py) / [intervals.py](intervals.py) | Intraday session + multi-timeframe handling |
| [loader.py](loader.py) / [signal_source.py](signal_source.py) | Bar + signal sourcing for runs |
| [paper/](paper/) | Forward paper-trading engine and its durable state |
| [fees.py](fees.py) | `FeeModel` / `SlippageModel` Protocols + 5 default implementations + name registries |
| [evaluator.py](evaluator.py) | `Evaluator` Protocol + `StandardEvaluator` (returns canonical metrics) |
| [backtester.py](backtester.py) | `Backtester.run(strategy, config) -> RunResult` — the orchestrator |
| [registry.py](registry.py) | `agent_runs` CH writer/reader for run history + reproducibility |
| [permutation.py](permutation.py) | MCPT bar-permutation kernel — multi-market shared shuffle, walk-forward `start_after` |
| [significance.py](significance.py) | `mcpt_pvalue` + `benjamini_hochberg` — the statistical promotion gate |
| [strategies/sma_crossover.py](strategies/sma_crossover.py) | Canary — long-only SMA crossover. Interval-agnostic. |
| [strategies/llm_agent.py](strategies/llm_agent.py) | LLM-driven strategy. Wraps Claude via the Anthropic SDK. Response-cached in local SQLite for replay reproducibility + cost control. |

## How to add a new strategy

1. Create `strategies/<name>.py`.
2. Either subclass `BaseStrategy` (for rule-based) or implement the
   `Strategy` Protocol directly (for LLM/RL/exotic cases).
3. Set `name`, `version`, `interval` class attributes.
4. Optionally define a `<Name>Params` Pydantic model and store it
   as `self.params`.
5. Implement `on_bar(self, ctx) -> Action`. Reach indicators via
   `ctx.indicator("sma", period=N)` — never import indicator
   classes directly.
6. Write tests in [`strategies/tests/`](strategies/tests/) against synthetic bar streams (strategies
   are pure → trivial to test).

## How to add a new indicator

See [app/indicators/README.md](../../indicators/README.md). Subclass
`Indicator`, add one entry to `app/indicators/registry.py`. Existing
strategies don't change.

## How to add a new fee or slippage model

Implement the relevant Protocol in `fees.py`, add to the registry
dict at the bottom of that file. Backtest configs reference by
name (`fees_model: "your_name"`).

## How to run a backtest

```bash
poetry run python scripts/run_backtest.py --config configs/canary.yaml
```

The CLI loads the config, instantiates the strategy by name from
the registry, runs the backtester, prints a metrics table, and
writes one row to `agent_runs`. Re-running the same config
produces an identical metrics row (reproducibility test).

## Modularity contracts (enforced by tests)

1. **Strategies are pure.** No `app.db.*`, `app.providers.*`,
   `httpx`, `requests` imports in `strategies/*.py`. Enforced by
   `test_strategy_is_pure`.
2. **Backtester is deterministic.** Same inputs → same outputs.
   Enforced by `test_backtester_is_deterministic`.
3. **Strategy.interval matches config.interval.** Backtester
   raises `ValueError` on mismatch — prevents silent downsampling.
4. **Fills happen on the next bar's open by default.** Prevents
   look-ahead bias. Configurable via `SlippageModel`.
5. **Reproducibility pinning.** Every `RunResult` carries
   `snapshot_id` (Iceberg) + `git_sha` + `strategy_version` +
   `strategy_params` + `config`. Re-running with the same triple
   produces the same metrics.

## Multi-timeframe (TA-4.1 onward)

Strategies that need multiple bar timeframes declare:

```python
class MyMtfStrategy(BaseStrategy):
    name = "my_mtf"
    version = "0.1"
    interval = "1h"             # execution (finest)
    intervals = ["1d", "1h"]    # full list, coarsest-to-finest
```

The Context exposes each via:

- `ctx.history` — execution-interval (back-compat property)
- `ctx.history_at("1d")` — explicit interval
- `ctx.indicator("sma", period=200, interval="1d")` — cross-interval indicator

**No look-ahead invariant**: coarser-interval bars are only visible
when `coarser_bar.timestamp + interval_duration <= execution_bar.timestamp`.
The Backtester enforces this via `advance_coarser` ordering;
[`tests/test_multi_timeframe.py`](tests/test_multi_timeframe.py) in
`test_backtester_releases_coarser_bars_only_when_ready`
pins it.

Single-TF strategies (only `interval`, no `intervals` attribute)
work unchanged — the harness wraps them as `[interval]` internally.

## Risk and portfolio gating

`Backtester.run()` runs one symbol; **`Backtester.run_portfolio()`** runs a
shared-portfolio multi-symbol book with `RiskManager` gating on top.

Conviction sizing across many concurrent long and short positions compounds
into unacceptable drawdown if ungated, so `RiskManager` caps two things:

| Cap | Meaning |
|---|---|
| `max_concurrent` | Don't be in everything at once |
| `max_portfolio_heat` | Total open risk (Σ entry→stop $ risk) ≤ heat × equity |

**Portfolio heat is the real lever** — it bounds how much of the book is at
risk simultaneously regardless of per-trade size. The manager is stateful: the
backtester registers a position's risk when an entry is allowed and releases it
on exit. One position per symbol, so per-name capping is implicit.

`BacktestConfig` also carries a **drawdown brake** (`dd_brake_limit`,
`dd_brake_floor`) that scales exposure down as drawdown deepens, and a
short gate for dynamic universes (short only the weakest N by as-of momentum,
enabling long-leaders / short-laggards).

## What's NOT in the current build

- **Live execution** — paper trading is the forward record; no order routing.
- **On-the-fly minute→coarser resampling inside the backtester.** The
  `BarReader` handles this for the CH live tier; research tables are
  pre-materialized by `scripts/build_ohlcv_*.py`.

## Before you trust a number

Backtest results are **not** a track record. Any return claim needs the MCPT
gate in [`significance.py`](significance.py) /
[`permutation.py`](permutation.py), pre-registered in
[`docs/research/hypotheses.md`](../../../docs/research/hypotheses.md) **before**
the sweep. Canonical results:
[`docs/research/findings.md`](../../../docs/research/findings.md) —
note that **all pre-EXP-26 performance claims are dead** (data contamination).
Methodology: [`docs/research/mcpt_methodology.md`](../../../docs/research/mcpt_methodology.md).
No-look-ahead audit:
[`docs/research/no_lookahead_audit.md`](../../../docs/research/no_lookahead_audit.md).
