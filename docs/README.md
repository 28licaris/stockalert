# StockAlert — Planning Docs

The architecture, plans, and live design docs for the data platform +
AI trading system. Code lives under `app/`, `tests/`, `scripts/`; this
folder holds the "why" + "where we're going".

## If you're picking up the build

The detailed journal is the commit log — `git log origin/main`. The
v2 architecture migration shipped on `v2-architecture` in late May
2026 (commits prefixed `CV1`-`CV18`).

For codebase orientation, in this order:

1. [`../CLAUDE.md`](../CLAUDE.md) — the repo guide. Layer map, lake
   table inventory, standards entry points.
2. [`architecture_v2/`](architecture_v2/README.md) — the canonical
   v2 lake + ML design (schemas, S3 layout, Spark, providers,
   migration log, runbook, decision gates). **Source of truth** for
   anything lake / ingest / ML related.
3. [ARCHITECTURE.md](ARCHITECTURE.md) — system-wide bounded-service
   overview. Predates the v2 migration; lake sections defer to
   architecture_v2/ when they conflict.
4. [STARTUP_FLOW.md](STARTUP_FLOW.md) — what `uvicorn app.main_api:app`
   does at boot; how to verify each subsystem is alive.
5. [trading_subsystem_design.md](trading_subsystem_design.md) —
   implementation contract for the sim/backtest layer. Pydantic
   shapes, Protocols, folder structure.
6. [trading-ai-build-plan.md](trading-ai-build-plan.md) — AI trading
   roadmap (strategy framework, signals, execution).
7. [indicator_exposure_design.md](indicator_exposure_design.md) —
   how technical indicators are computed + served (dashboard, MCP
   agents, backtester).
8. [elliott_wave_plan.md](elliott_wave_plan.md) — EW structural
   analysis as indicators + screener rules + RL features. Plan only.
9. [frontend_plan.md](frontend_plan.md) — the cockpit SPA (React +
   TanStack + shadcn/ui). Built SaaS-ready.
10. [risk_management_plan.md](risk_management_plan.md) — risk layer
    between strategies and execution. Plan only; prereq for any
    paper / live execution.
11. [futures_data_plan.md](futures_data_plan.md) — futures (ES, NQ,
    CL, GC) ingestion. Plan only.
12. [iceberg_performance_findings.md](iceberg_performance_findings.md) —
    historical write-up of v1's PyIceberg upsert investigation.
    Most of these findings drove the v2 partitioning (bucket(32, symbol))
    + Spark-based whole-market batch jobs.
13. [data_ingestion_paths.md](data_ingestion_paths.md) — the data
    paths into the system. Originally written for v1; the v2
    equivalent is in [`architecture_v2/01_architecture.md`](architecture_v2/01_architecture.md).
14. [streaming_universe_model.md](streaming_universe_model.md) —
    operational model: Schwab = only streaming provider; two-tier
    universe; the add-streamed-symbol flow.
15. [assistant_plan.md](assistant_plan.md) — the chat copilot
    (LLM-backed agent over MCP tools): backend master plan.
16. [assistant_chat_interface.md](assistant_chat_interface.md) — the
    chat interface end-to-end (browser ⟷ server ⟷ model round-trip),
    production-readiness checklist, and alternative model architectures.
17. [frontend_api_contracts.md](frontend_api_contracts.md) —
    Pydantic contracts the cockpit consumes.
18. [customer_identity_and_subscription_spec.md](customer_identity_and_subscription_spec.md) —
    production customer authentication, tenant isolation, PostgreSQL account
    data, Stripe subscriptions, and customer/operator dashboard separation.

## Working agreement

- **Spec-first** (per [`standards/engagement.md`](standards/engagement.md)).
  Restate, confirm, write.
- **No silent failures** (per [`standards/coding.md`](standards/coding.md)).
- **The commit log is the journal.** Detailed reasoning lives in
  commit messages (`git log --grep CV` for the v2 migration).
- **Every microservice folder has a `README.md`** describing what it
  does, what it owns, its contract.
- **Docs stay current with code.** Adding a service or changing an
  architectural boundary updates the relevant plan doc in the same
  change.

## Where the rest of the docs live

- [`../README.md`](../README.md) — repo entry point (setup, run commands).
- [`../CLAUDE.md`](../CLAUDE.md) — repo guide (this is what Claude reads).
- [`../CONFIG.md`](../CONFIG.md) — operator config reference.
- [ISSUES.md](ISSUES.md) — bug + flaky-test tracker.
- [COMMANDS.md](COMMANDS.md) — operator cheatsheet.
- [`standards/`](standards/README.md) — coding / engagement / testing rules.
- [`architecture_v2/07_runbook.md`](architecture_v2/07_runbook.md) —
  operator procedures for the v2 lake (bulk-imports, Spark jobs,
  monitoring, DR).
