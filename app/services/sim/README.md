# sim/

Trading subsystem — backtest harness, strategy framework, portfolio
accounting, evaluator, and run registry that all agents (LLM, RL,
rule-based) plug into.

**Implementation contract:**
[docs/trading_subsystem_design.md](../../../docs/trading_subsystem_design.md).
Read it before changing anything here.

**Strategic roadmap:**
[docs/trading-ai-build-plan.md](../../../docs/trading-ai-build-plan.md).

## Layout

| File | Owns |
|---|---|
| [schemas.py](schemas.py) | `Action`, `Position`, `Trade`, `RunMetrics`, `RunResult`, `BacktestConfig`, `Bar` Protocol, `PortfolioSnapshot` |
| [strategy.py](strategy.py) | `Strategy` Protocol + `BaseStrategy` convenience class |
| [context.py](context.py) | `Context` (per-bar view passed to strategies) + `BarHistory` |
| [portfolio.py](portfolio.py) | `Portfolio` — cash, positions, equity curve, trade log |
| [fees.py](fees.py) | `FeeModel` / `SlippageModel` Protocols + 5 default implementations + name registries |
| [evaluator.py](evaluator.py) | `Evaluator` Protocol + `StandardEvaluator` (returns canonical metrics) |
| [backtester.py](backtester.py) | `Backtester.run(strategy, config) -> RunResult` — the orchestrator |
| [registry.py](registry.py) | `agent_runs` CH writer/reader for run history + reproducibility |
| [strategies/](strategies/) | Concrete strategies — one file each, pluggable |

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
6. Write tests in `tests/` against synthetic bar streams (strategies
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

## What's NOT in TA-1

- Multi-symbol shared portfolio (each `Backtester.run()` runs one
  symbol at a time; TA-3 adds shared-portfolio multi-symbol).
- Resampling 1m bronze to 5m/15m/30m/1h/4h (TA-3).
- Short selling (TA-5+ alongside RL agent).
- Live execution (TA-6+).
- LLM-driven strategy (TA-2 — comes next).

Don't add these in code until the journal opens the phase.
