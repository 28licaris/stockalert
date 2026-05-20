# Architecture v2 — Implementation Reference

**Status:** Design locked, awaiting Phase-1 implementation approval.

This folder is the **complete reference** for the v2 architecture
refactor. Each file is one topic, self-contained, readable in any
order. Read [`01_architecture.md`](01_architecture.md) first if you
want the big picture, then drill into the file for the area you're
working on.

The earlier single-file `docs/standards/data/architecture_v2.md`
(commit `9c3fbc9`) is now a high-level summary; this folder is the
authoritative deep dive.

## Why v2 exists

The current ("v1") architecture uses a medallion pipeline
(bronze → silver → CH) optimized for whole-universe nightly batch
work. It has two production-grade pain points:

1. **Cold-start on-add is slow.** Adding a brand-new symbol requires
   building silver for that one symbol over 5 years — currently
   ~3 hours because the silver build scans whole-market bronze files
   to extract one symbol's data. Charts show "empty" for 3h after
   click. Not acceptable for the live-charting UX.

2. **Mixed semantics in bronze.** Polygon flat-files give raw bars;
   Polygon REST `/aggs` returns adjusted bars; Schwab returns
   adjusted bars. Writing all three to the same "bronze" table
   breaks the "bronze is raw" invariant.

v2 fixes both by **splitting the live tier from the ML tier**:

- **Live (CH)** — Schwab WS + REST writes directly, no adjustment math
  needed (Schwab is already adjusted). Add-symbol latency ≤5s.
- **Lake (S3 + Iceberg)** — three datasets that share schema for
  Spark/DuckDB analytics: `polygon_raw` (immutable bulk), `polygon_adjusted`
  (one-time adjustment build), `schwab_universe` (lake mirror of live).

## Contents

| File | Topic |
|---|---|
| [01_architecture.md](01_architecture.md) | System architecture, tiers, datasets, ingest paths, read paths, failure isolation |
| [02_schema.md](02_schema.md) | Canonical OHLCV schema, Iceberg DDL, column semantics, adj_factor explained |
| [03_s3_layout.md](03_s3_layout.md) | Concrete S3 paths, Iceberg metadata structure, partition strategy rationale, file sizing |
| [04_spark.md](04_spark.md) | PySpark setup, get_spark() helper, real queries (single-symbol, joins, time-travel, incremental), EMR Serverless config |
| [05_providers.md](05_providers.md) | DataProvider interface, Schwab vs Polygon vs Alpaca split, swap-in strategy |
| [06_migration.md](06_migration.md) | The 5-phase migration plan, commit-by-commit checklist, rollback procedures |
| [07_runbook.md](07_runbook.md) | Operator procedures: running adjustments, restoring CH from lake, monitoring, cost watching |
| [08_decisions.md](08_decisions.md) | Open decisions awaiting approval (naming, partition strategy, compute platform, etc.) |

## Status

- [x] v1 (medallion) implemented and operational
- [x] v2 design documented in this folder
- [ ] Phase 1: Create v2 Iceberg tables + one-time polygon_adjustment_job
- [ ] Phase 2: live_lake_writer → data.schwab_universe
- [ ] Phase 3: On-add hot path direct Schwab REST → CH (cuts over from silver build)
- [ ] Phase 4: /api/v1/lake/bars endpoint via DuckDB
- [ ] Phase 5: Drop legacy bronze.*/silver.* (T+30d after v2 stable)

## Approval gates (block Phase 1)

The 7 gates from [`08_decisions.md`](08_decisions.md) need operator
sign-off before any v2 code changes land. Phases are each independently
reversible; this folder + sign-off → implementation can resume.

## How to use this folder

| If you're... | Read... |
|---|---|
| Implementing Phase N | [`06_migration.md`](06_migration.md) for that phase, then the relevant topic doc |
| Writing a Spark job | [`04_spark.md`](04_spark.md) |
| Adding a new provider | [`05_providers.md`](05_providers.md) |
| Debugging an ingest | [`07_runbook.md`](07_runbook.md) |
| Onboarding to the system | [`01_architecture.md`](01_architecture.md) start-to-finish |
| Reviewing the design | [`08_decisions.md`](08_decisions.md) |
