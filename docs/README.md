# StockAlert — Planning Docs

Start here. This folder holds the architecture, plans, and live progress
log for the data platform + AI trading system. Other folders (`app/`,
`tests/`, `scripts/`) hold code.

## If you're picking up the build

**Read [BUILD_JOURNAL.md](BUILD_JOURNAL.md) first.** It tracks every
phase, what's done, what's pending, the gate test for each phase, and
every architectural decision with its reason. The Decision log at the
bottom is the authoritative history.

Then, in this order:

1. [ARCHITECTURE.md](ARCHITECTURE.md) — system-wide view, 13 bounded
   services with explicit contracts, current state → target deployment.
2. [STARTUP_FLOW.md](STARTUP_FLOW.md) — exactly what happens when the
   FastAPI process boots; how to verify each subsystem is running.
3. [data_platform_plan.md](data_platform_plan.md) — storage + ingestion
   (S3 + Iceberg + Glue, bronze/silver/gold).
4. [trading-ai-build-plan.md](trading-ai-build-plan.md) — AI trading
   services structured as deployable units with Pydantic contracts.
5. [trading_subsystem_design.md](trading_subsystem_design.md) —
   **implementation contract** for the trading subsystem: Pydantic
   shapes, Protocols, folder layout, modularity guarantees. Read this
   before writing any code under `app/services/sim/`.
6. [indicator_exposure_design.md](indicator_exposure_design.md) —
   how technical indicators get computed and served to the dashboard,
   MCP agents, and the backtester (single `IndicatorReader` source of
   truth; Pattern A now, gold-tier pre-compute deferred to Phase 6).
7. [elliott_wave_plan.md](elliott_wave_plan.md) — investigation +
   phased plan for incorporating Elliott Wave structural analysis as
   indicators, screener rules, strategies, and training-track features
   (LLM + RL). Plan only; no code yet.
8. [frontend_plan.md](frontend_plan.md) — the developer's cockpit
   plan: evolve the static-HTML dashboard into a typed React +
   TanStack + shadcn/ui SPA that exposes every backend capability
   (screener, backtests, MCP tools, Iceberg lake, runs registry).
   Plan only; no code yet.

## Working agreement

- **Don't move to the next phase until the current phase's gate test
  is green.** The gate is named in the journal for each phase.
- **Every architectural decision goes in the journal Decision log**
  with a date and a reason. Future agents shouldn't have to guess why
  a path was taken.
- **Every microservice folder has a `README.md`** describing what it
  does, what it owns, its contract, and how to test it. New service →
  README in the same change as the code.
- **Docs stay current with code.** Adding a service or changing an
  architectural boundary updates the relevant plan doc(s) and the
  journal in the same change, not later. Drift here breaks the
  pick-up-where-we-left-off promise.
- **Cross-doc references use markdown links** with relative paths so
  links work from any clone.
- **Code references use `../`** (e.g., `../app/services/...`) because
  this folder sits one level below the repo root.

## Doc relationships

```
README.md (this file)
  ├── BUILD_JOURNAL.md         ← progress + decisions; the live source of truth
  ├── ARCHITECTURE.md          ← system overview; refers to the two plans below
  ├── STARTUP_FLOW.md          ← what the FastAPI process does at boot
  ├── data_platform_plan.md           (data side: strategic roadmap)
  ├── trading-ai-build-plan.md        (trading side: strategic roadmap)
  ├── trading_subsystem_design.md     (trading side: implementation contract)
  ├── indicator_exposure_design.md    (indicator delivery architecture)
  ├── elliott_wave_plan.md            (EW structural analysis — plan only)
  └── frontend_plan.md                (developer cockpit SPA — plan only)
```

`ARCHITECTURE.md` is the high-level overview. The two plan docs go deep
on their respective tracks. The journal supersedes plan-doc detail when
they conflict — plans get loosely revised over time; the journal is
where the ground truth lives.

## Where the rest of the docs live

- `../README.md` — repo entry point (setup, run commands).
- `../CONFIG.md` — operator config reference (env vars, defaults).
- [ISSUES.md](ISSUES.md) — bug + flaky-test tracker.
- [COMMANDS.md](COMMANDS.md) — copy-paste cheatsheet for the commands
  you actually run (docker, pytest, AWS, ClickHouse, Schwab OAuth).
