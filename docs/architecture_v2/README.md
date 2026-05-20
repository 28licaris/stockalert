# Architecture v2 — Implementation Reference

**Status:** Design locked, awaiting Phase-1 implementation approval.

This folder is the **complete reference** for the v2 architecture
refactor. Each file is one topic, self-contained, readable in any
order. Read [`01_architecture.md`](01_architecture.md) first if you
want the big picture — it opens with a comprehensive Mermaid flow
diagram covering every source, tier, compute job, and read surface —
then drill into the file for the area you're working on.

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
| [08_decisions.md](08_decisions.md) | Approved decisions log (gates 1-14: lake/Spark infra, ML pipeline, scope) |
| 09 — *(reserved for future scalability doc; see note in `10_ml_pipeline.md`)* | |
| [10_ml_pipeline.md](10_ml_pipeline.md) | ML pipeline: features, labels, training, inference, monitoring, snapshot pinning |

## Status

- [x] v1 (medallion) implemented and operational
- [x] v2 design documented in this folder
- [x] All 14 approval gates signed off 2026-05-20 (see [`08_decisions.md`](08_decisions.md))
- [ ] Phase 1: Create v2 Iceberg tables + one-time polygon_adjustment_job
- [ ] Phase 2: live_lake_writer → equities.schwab_universe
- [ ] Phase 3: On-add hot path direct Schwab REST → CH (cuts over from silver build)
- [ ] Phase 4: /api/v1/lake/bars endpoint + MCP tool wrappers via DuckDB
- [ ] Phase 5: Drop legacy bronze.*/silver.* (T+7d after v2 stable)

## Approval gates

All 14 gates in [`08_decisions.md`](08_decisions.md) are approved.
Gates 1-7 cover lake/Spark infra; 8-11 cover the ML pipeline (block
first model training, not the lake build itself); 12-14 cover backfill
scope, universe seed, and CH schema. Phases are each independently
reversible.

Three non-default decisions to remember when reading the design docs:
namespace is `equities` (not `data`), Phase 5 quarantine is 7 days
(not 30), and the lake-read endpoint ships with MCP tool wrappers
(CV12b in [`06_migration.md`](06_migration.md)).

## How to use this folder

| If you're... | Read... |
|---|---|
| Implementing Phase N | [`06_migration.md`](06_migration.md) for that phase, then the relevant topic doc |
| Writing a Spark job | [`04_spark.md`](04_spark.md) |
| Adding a new provider | [`05_providers.md`](05_providers.md) |
| Debugging an ingest | [`07_runbook.md`](07_runbook.md) |
| Onboarding to the system | [`01_architecture.md`](01_architecture.md) start-to-finish |
| Reviewing the design | [`08_decisions.md`](08_decisions.md) |
