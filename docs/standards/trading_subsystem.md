# Trading Subsystem — Enforcement Checklist

The trading subsystem in `app/services/sim/` is a **research +
backtesting harness** that all agents (LLM, RL, rule-based) plug into.

The full implementation contract lives in
[`../trading_subsystem_design.md`](../trading_subsystem_design.md).
**This file is the enforcement checklist** for code reviews and AI
contributions.

## When this applies

Any change to:

- `app/services/sim/*`
- `app/services/sim/strategies/*`
- `app/indicators/*`

…must obey these rules. Code that breaks them is a regression even if
tests pass.

## The four pluggability axes

1. **Strategies are pluggable.** Implement the `Strategy` Protocol
   (`name`, `version`, `interval`, `on_bar(ctx) -> Action`). Add to
   `strategies/`. Done. The harness must not change.

2. **Timeframes are pluggable.** A `Strategy.interval` field declares
   what bars the strategy needs. The backtester resolves the right bar
   source. Swapping `1d` for `5m` requires zero strategy code changes.

3. **Indicators are pluggable.** Subclass `Indicator(ABC)`, register in
   `INDICATOR_REGISTRY` by string name. Strategies request them via
   `ctx.indicator("sma", period=20)` — never by importing the indicator
   class directly.

4. **Fees / slippage / evaluator are pluggable.** Each is a Protocol
   with a default implementation. Backtester accepts them per-run.

## Hard rules

### Strategies are pure with respect to platform state

No imports of `app.db.*` or `app.providers.*` from `strategies/*.py`.
They CAN call external SDKs (Anthropic, OpenAI, broker APIs) — those
do not violate the platform layering, just the "deterministic given
bars" property, which the cache restores.

Enforced by `test_strategy_is_pure`.

### LLM-driven strategies cache on prompt hash + use temperature=0

- Cache key: `sha256(model || system_prompt || user_prompt)`.
- Same prompt → cache hit → zero API cost on replay.
- Bumping `strategy.version` is the explicit signal to invalidate; the
  cache is otherwise append-only.
- With `temperature=0` + a stable prompt, the same backtest produces an
  identical `agent_runs` row twice.

Enforced by `test_llm_agent_replay_produces_identical_decisions`.

### LLM errors degrade to `hold()`

API failure, parse failure, missing key — log a warning, increment a
stat counter, emit `hold()`. The backtest completes with whatever bars
succeeded; agents see a real metrics row even after partial failures.

The alternative (raise) would mask real strategy quality behind
transient infra noise.

### Fills happen on the NEXT bar's open by default

Prevents look-ahead bias. Configurable via `SlippageModel` for
strategies that need finer control.

### Same inputs → same metrics

Every backtest run pins:

- Iceberg snapshot ID
- Strategy version
- Serialized params
- Config

A `reproduce(run_id)` CLI verifies identity. Enforced by
`test_backtester_is_deterministic`.

### Strategy state is local; harness state is global

Strategy owns its internal state. Harness owns the portfolio. They
communicate only via:

- `Context` (read-only for strategy)
- `Action` (read-only for harness)

Don't bypass.

### `agent_runs` is the canonical history

Every backtest writes one row with snapshot ID + params + metrics. The
`git_sha` column captures code version. Re-running from a row should
produce the same metrics.

### LLM strategies cache on `(symbol, ts, context_hash)`

A replay must not re-pay the API cost. The cache key is part of the
contract — change it and you break replay determinism.

## Folder rules

Per [`service_modules.md`](service_modules.md):

- Cross-service imports come from `schemas.py` or `strategy.py` (the
  Protocol module). Never from `backtester.py`, `portfolio.py`, or
  `strategies/*.py`.
- `from_settings()` for anything that touches `app.config`.
- Pydantic models for all params; no `**kwargs` magic for strategy
  configuration.

## What "swing-first, day-later" actually means

The project-stated goal is swing-trading on daily bars first, then
day-trading on intraday bars. **This is a sequencing decision, not a
design constraint.** The abstractions are identical — only
`config.interval` differs.

If you find yourself writing swing-specific code that would not work
for day-trading, you have broken the modularity contract.

## Related

- [`../trading_subsystem_design.md`](../trading_subsystem_design.md) —
  full implementation contract.
- [`service_modules.md`](service_modules.md) — folder template.
- [`platform_design.md`](platform_design.md) — three-layer TA
  (indicators → signals → strategies).
