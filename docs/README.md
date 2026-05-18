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
   **Built SaaS-ready** — auth / tenancy / billing / quota seams
   land in FE-1 so a future subscription-service flip is purely
   additive (~2 weeks of integration vs. a 6-8 week refactor).
   Plan only; no code yet.
9. [silver_layer_plan.md](silver_layer_plan.md) — implementation
   contract for the silver tier: provider-merged, corp-action-
   adjusted, dedup'd OHLCV. Phases TA-5.0 (corp-actions) → TA-5.1
   (build job) → TA-5.2 (SilverReader) → TA-5.3 (silver→CH backfill
   that replaces today's provider-REST backfill on `add_members`,
   making the cockpit warming-up UX feasible) → TA-5.4 (shadow
   validation) → TA-5.5 (retire provider-REST paths). Plan only;
   no code yet.
10. [risk_management_plan.md](risk_management_plan.md) — risk-
    management layer that sits between strategies and any executor.
    8 rules (kill-switch, daily-loss halt, max-DD halt, max-position-
    size, max-concentration, max-leverage, ATR-volatility sizing,
    cooldown) composed into a `RiskPolicy`. Phases TA-R.1..TA-R.6
    (~10-12 days). **Prerequisite for any paper or live execution.**
    Plan only; no code yet.
11. [SYSTEM_REVIEW_2026-05-17.md](SYSTEM_REVIEW_2026-05-17.md) —
    independent senior-quant-engineer-style audit identifying 7
    profitability-gaps (risk mgmt, survivorship bias, weak strategy
    evidence, regime context, harness tests, execution layer, live
    observability). Drives the additions in `trading_subsystem_design.md`
    §10 phasing.
12. [data_ingestion_paths.md](data_ingestion_paths.md) — the
    definitive map of every path data takes into the system (8 paths
    across S3/Iceberg bronze, silver, and ClickHouse). Per-path
    walkthrough, master ASCII diagram, per-provider adjustment-status
    table, audit + monitoring touchpoints, the ground-truth rule.
    Updated 2026-05-17 with TA-5.0 + TA-5.7 ingest paths.
13. [CHECKPOINT_2026-05-17.md](CHECKPOINT_2026-05-17.md) — session
    pause-point notes: what TA-5.0 + TA-5.7 delivered, empirical
    state of the live system, operator-validation steps required
    before TA-5.1, where to pick up next.
14. [streaming_universe_model.md](streaming_universe_model.md) —
    concise operational model: Schwab = only streaming provider;
    two-tier universe (seed vs ad-hoc archive); the "add streamed
    symbol" flow; promote-to-seed CLI; Polygon-pause/resume
    behavior. Quick-reference companion to silver_layer_plan.
15. [data_flow_review_2026-05-17.md](data_flow_review_2026-05-17.md) —
    end-to-end review of the data flow against operator intent.
    Identifies 6 gaps + 5-phase plan to close them (G1 dynamic
    universe; G2 silver OHLCV build; G3 nightly schedule; G4
    silver→CH backfill; G5 delete legacy + wipe-rebuild; G6
    gap-handling UX). Includes 4 operator decisions to confirm.
16. [runbook_silver_ohlcv_build.md](runbook_silver_ohlcv_build.md) —
    operator runbook for TA-5.1.7: 5-step procedure to validate
    + initial-backfill silver from bronze. Pre-flight script,
    multi-hour `--full` run, post-run verification, nightly-loop
    enablement, Yahoo-adj spot-check.
17. [futures_data_plan.md](futures_data_plan.md) — plan-only
    investigation for adding futures (ES, NQ, CL, GC, …) to the
    pipeline. Three phases: TF-1 live-only via Schwab CHART_FUTURES
    (~3 days), TF-2 Polygon Futures historical (~3-5 days), TF-3
    continuous-contract rollover series (~5-7 days). Documents the
    fundamental difference from equities (no historical tip-fill
    via Schwab REST), open design questions, and decision gates
    between phases. No code yet.

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
  ├── frontend_plan.md                (developer cockpit SPA — plan only)
  ├── silver_layer_plan.md            (silver tier implementation — plan only)
  ├── risk_management_plan.md         (risk layer — plan only; prereq for execution)
  └── SYSTEM_REVIEW_2026-05-17.md     (audit + 7 profit-grade gaps)
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
