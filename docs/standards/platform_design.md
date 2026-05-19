# Platform Design — Intent and Principles

Modular monolith on a deliberate path to a microservice platform for
**AI/ML-driven equities day/swing trading**. Every contribution
preserves the lift-out path without committing to it today.

## Target service map

```
feature-server     bronze/silver bars → model features
discovery-jobs     backfills, screening, training data prep
screener           "interesting today" picks for agents
simulation         backtest engine
agent-runtime      LLM/RL inference loop
execution          order placement (paper → live)
evaluator          PnL, hit rate, drawdown
monitoring         health, drift, kill switches
api-gateway        REST + MCP entry (today's main_api.py)
```

Today: one FastAPI process. Containers when they need to — not before,
not under pressure.

## Principles

1. **Contract-first.** Every read/write surface starts as a Pydantic
   schema. Callers import `schemas.py` / `contract.py`, never
   `service.py`.

2. **Agent-readiness is first-class.** Anything an agent might read
   gets: a read service + Pydantic contract, an HTTP route, an MCP
   tool. Tools and routes are thin adapters over the same service —
   never duplicate logic between them.

3. **Lake reads work without ClickHouse.** Historical / training reads
   go straight to Iceberg via readers. CH is live/recent only.

4. **Three-layer TA:** `indicators/` (pure math) → `signals/`
   (pattern detectors, pure fns) → `strategies/` (compose). Don't
   conflate.

5. **Medallion: bronze → silver → gold.** Bronze append-only (see
   [`data/bronze_idempotency.md`](data/bronze_idempotency.md)). Silver
   lean and canonical (see [`data/lean_silver.md`](data/lean_silver.md)).
   Gold = pre-computed features pinned to snapshots.

6. **Hot/cold split.** ClickHouse (hot, seconds-fresh) and Iceberg
   (cold, T+1) share no runtime deps. Either can be off.

7. **Startup isolation.** `_safe_start()` in `main_api.py` wraps every
   non-foundation subsystem.

8. **Reproducibility.** Every saved model logs the Iceberg snapshot ID
   it trained on. `model_training_runs` registry is authoritative.

9. **Provider abstraction = config param, not service boundary.**
   Service folders organize by domain (`bronze/`, `ingest/`, `live/`),
   not provider. Multi-provider via factory methods.

10. **Doc discipline.** See [`doc_discipline.md`](doc_discipline.md).

## Three test questions for any architectural call

1. **Lift-out:** can this still work as its own container next month?
2. **Agent:** can an LLM agent use this without bespoke integration?
3. **Replay:** can a training run 6 months out reproduce this
   bit-for-bit?

Any "no" → design is incomplete. Surface before coding.
