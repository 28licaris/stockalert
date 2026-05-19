# Trading Subsystem — Enforcement Checklist

Full design: [`../trading_subsystem_design.md`](../trading_subsystem_design.md).
This file is the enforcement checklist.

Applies to `app/services/sim/*`, `app/services/sim/strategies/*`,
`app/indicators/*`. Breaking these rules is a regression even if tests
pass.

## Pluggability axes

1. **Strategies** — implement `Strategy` Protocol (`name`, `version`,
   `interval`, `on_bar(ctx) -> Action`). Add to `strategies/`. Harness
   unchanged.
2. **Timeframes** — `Strategy.interval` declares needed bars.
   Backtester resolves the source. Daily ↔ 5m = zero strategy code
   change.
3. **Indicators** — subclass `Indicator(ABC)`, register in
   `INDICATOR_REGISTRY` by string. Strategies request via
   `ctx.indicator("sma", period=20)` — never by direct import.
4. **Fees / slippage / evaluator** — each is a Protocol with a default.
   Backtester accepts per-run.

## Hard rules

- **Strategies pure w.r.t. platform state.** No `app.db.*` /
  `app.providers.*` imports from `strategies/*.py`. External SDKs
  (Anthropic, broker APIs) are allowed — the cache restores
  determinism. Enforced by `test_strategy_is_pure`.

- **LLM strategies: cache on prompt hash + `temperature=0`.** Cache
  key = `sha256(model || system_prompt || user_prompt)`. Same prompt
  → cache hit → zero API cost on replay. Bump `strategy.version` to
  invalidate.

- **LLM errors degrade to `hold()`.** Log warning + stat counter, emit
  `hold()`. Never raise — backtest must complete and produce a metrics
  row.

- **Fills on NEXT bar's open by default.** Prevents look-ahead.
  Configurable via `SlippageModel`.

- **Same inputs → same metrics.** Pin Iceberg snapshot ID + version +
  params + config. `reproduce(run_id)` CLI verifies. Enforced by
  `test_backtester_is_deterministic`.

- **State boundary.** Strategy owns strategy state. Harness owns
  portfolio. Communicate only via `Context` (read-only for strategy)
  and `Action` (read-only for harness).

- **`agent_runs` is canonical history.** One row per backtest:
  snapshot ID + params + metrics + `git_sha`.

## Folder rules

Per [`service_modules.md`](service_modules.md):

- Cross-service imports from `schemas.py` or `strategy.py` only.
- `from_settings()` for `app.config` touches.
- Pydantic models for all params — no `**kwargs`.

## "Swing-first, day-later"

A sequencing decision, not a design constraint. Abstractions are
identical — only `config.interval` differs. Swing-specific code that
wouldn't work for day-trading = broken contract.
