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
  ├── data_platform_plan.md
  └── trading-ai-build-plan.md
```

`ARCHITECTURE.md` is the high-level overview. The two plan docs go deep
on their respective tracks. The journal supersedes plan-doc detail when
they conflict — plans get loosely revised over time; the journal is
where the ground truth lives.

## Where the rest of the docs live

- `../README.md` — repo entry point (setup, run commands).
- `../CONFIG.md` — operator config reference (env vars, defaults).
- [ISSUES.md](ISSUES.md) — bug + flaky-test tracker.
