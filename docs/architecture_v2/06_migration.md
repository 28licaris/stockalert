# 06 — Migration Plan (v1 → v2)

Five phases, each independently committable, each independently
reversible. The live tier stays operational on v1 until Phase 3 cuts
over.

## Pre-flight (one-time, no commits)

Verify state before starting:

- [ ] All `tests/test_*.py` and integration tests pass on `main`
- [ ] `data/.schwab_refresh_token` is valid (not expired)
- [ ] `STOCK_LAKE_BUCKET=s3://stockalert-lake` is set in env
- [ ] AWS credentials work (`aws s3 ls s3://stockalert-lake/`)
- [ ] Glue catalog accessible (`aws glue get-databases`)
- [ ] Gates 1-7 in [08_decisions.md](08_decisions.md) (lake/Spark infra) have operator sign-off — required before Phase 1
- [ ] Gates 8-12 in [08_decisions.md](08_decisions.md) (ML pipeline) — required before first model trains, not Phase 1

## Phase 1 — Additive: create v2 lake tables (no risk to live tier)

**Goal**: have the four new Iceberg tables existing + populated, with
the live tier still on v1.

| Commit | What | Verify by |
|---|---|---|
| **CV1** | `feat(lake): create v2 Iceberg tables with bucket partitioning` — DDL for `data.polygon_raw`, `data.polygon_adjusted`, `data.schwab_universe`, `data.market_corp_actions` | `aws glue get-tables --database-name data` shows all 4 |
| **CV2** | `feat(lake): one-time copy bronze.polygon_minute → data.polygon_raw` — Spark script that re-buckets the existing data | `SELECT count(*) FROM lake.polygon_raw` matches `bronze.polygon_minute` |
| **CV3** | `feat(lake): one-time copy bronze.polygon_corp_actions → data.market_corp_actions` | row-count parity |
| **CV4** | `feat(lake): polygon_adjustment_job + Spark scripts dir` — `scripts/spark/__init__.py`, `polygon_adjustment_job.py` | local Spark run on AAPL completes; row counts sane |
| **CV5** | One-time invocation of CV4 for full whole-market history → populates `data.polygon_adjusted` | `SELECT count(*) FROM lake.polygon_adjusted` ≈ count in raw |

After Phase 1: lake side is fully v2; live side is still v1.

### CV2 — concrete script outline

```python
# scripts/migrations/copy_bronze_to_data_polygon_raw.py
from scripts.spark import get_spark

spark = get_spark("migration_copy_polygon_raw")

# Read existing v1 bronze (month-only partitioning)
raw = spark.sql("SELECT * FROM lake.bronze.polygon_minute")

# Write to v2 layout (bucket(32, symbol), month(timestamp))
(raw.writeTo("lake.data.polygon_raw")
    .using("iceberg")
    .partitionedBy("bucket(32, symbol)", "month(timestamp)")
    .createOrReplace())
```

Wall-clock: ~1-2 hours on EMR Serverless for 120 GB.

### Rollback for Phase 1

If anything goes wrong:

```sql
DROP TABLE lake.data.polygon_raw;
DROP TABLE lake.data.polygon_adjusted;
DROP TABLE lake.data.schwab_universe;
DROP TABLE lake.data.market_corp_actions;
```

The v1 bronze/silver tables are untouched. Zero impact on live tier.

## Phase 2 — Redirect lake writers (still no risk to live tier)

**Goal**: `lake_archive_job` (was `live_lake_writer`) writes to
`data.schwab_universe` instead of `bronze.schwab_minute`.

| Commit | What | Verify by |
|---|---|---|
| **CV6** | `refactor(ingest): rename live_lake_writer → lake_archive_job; target data.schwab_universe` | After 1 hour of live stream, `SELECT count(*) FROM lake.schwab_universe WHERE source='schwab-live'` > 0 |
| **CV7** | Disable `nightly_schwab_refresh` job — Schwab WS + on-add tip-fill cover universe needs | `/api/v1/jobs` no longer shows `nightly_schwab_refresh` in active jobs |

### Rollback for Phase 2

```bash
# Re-enable the old job + roll back the rename
git revert <CV6,CV7 commits>
# Restart uvicorn to pick up the rollback
```

The old `bronze.schwab_minute` is still being written by uvicorn
during Phase 2 (we keep both during the transition window), so a
rollback just drops the new-side writes.

## Phase 3 — Live tier cuts over (the load-bearing change)

**Goal**: on-add hot path uses direct Schwab REST → CH (no silver
build). Latency gate <5s for new symbols.

| Commit | What | Verify by |
|---|---|---|
| **CV8** | `refactor(stream): on-add warmup direct Schwab REST → CH; bypass silver_build` | Add fresh symbol via `/api/v1/stream`; CH has 48d × 1-min + 20y × daily in <5s |
| **CV9** | `test(integration): v2 latency gate <5s — new file, alongside the v1 30s gate during the transition window` | `tests/integration/test_add_new_symbol_latency_v2.py` passes; the v1 `test_add_new_symbol_latency.py` is deleted in Phase 5 |
| **CV10** | Mark `silver_ohlcv_build` nightly job DISABLED in config; `silver.ohlcv_1m` becomes READ-ONLY | `/api/v1/jobs` no longer shows silver_ohlcv_build |

### Cutover procedure (run during low-traffic window)

1. Run all pre-flight tests on the current `main`.
2. Push CV8 to `main`. Verify uvicorn auto-reloads cleanly.
3. Run latency gate: `pytest tests/integration/test_add_new_symbol_latency.py -v`
4. If pass: push CV9 and CV10.
5. If fail: revert CV8 (one-line config flip in `stream_service.py`).

### Rollback for Phase 3

```bash
git revert <CV8 commit>
```

`stream_service.add()` reverts to the old warmup chain (silver build).
Latency goes back to ~3 hours but functionality is preserved.

## Phase 4 — Expose lake read path

**Goal**: `/api/v1/lake/bars` endpoint lets the cockpit (or operators)
read deep-history bars when zooming past CH's window.

| Commit | What | Verify by |
|---|---|---|
| **CV11** | `feat(api): /api/v1/lake/bars via DuckDB` — reads `data.polygon_adjusted` + `data.schwab_universe` UNION | curl returns rows for AAPL 2020 |
| **CV12** | `feat(cockpit): chart endpoint falls back to /lake/bars for zoom-out windows >CH retention` | Chart 10-year view of AAPL loads (was empty before) |

### Rollback for Phase 4

The lake-read endpoint is purely additive. If buggy, revert CV11 +
CV12 — chart loses the deep-zoom feature but everything else works.

## Phase 5 — Decommission v1

**Goal**: drop legacy bronze/silver tables. **Wait 30 days after
Phase 3 cutover with zero regressions before doing this.**

| Commit | What | Verify by |
|---|---|---|
| **CV13** | `chore(lake): DROP TABLE bronze.polygon_minute` (data lives in data.polygon_raw) | Confirm via `aws glue get-tables --database-name bronze` |
| **CV14** | `chore(lake): DROP TABLE bronze.schwab_minute` (data lives in data.schwab_universe) | Confirm |
| **CV15** | `chore(lake): DROP TABLE silver.ohlcv_1m` (not needed in v2 architecture) | Confirm |
| **CV16** | `chore(lake): DROP TABLE bronze.polygon_corp_actions` (data lives in data.market_corp_actions) | Confirm |
| **CV17** | `chore(tests): delete v1 latency gate test_add_new_symbol_latency.py (v2 gate has owned latency since Phase 3)` | File removed; CI green |
| **CV18** | `docs: archive v1 docs; symbol_lifecycle.md points to architecture_v2/` | Doc cross-refs updated |

### Rollback for Phase 5

Dropping Iceberg tables is **destructive**. To recover:
1. Re-create the table with the old DDL.
2. Re-run the migration scripts from Phase 1 (which copy from v2 back
   to v1 if reversed).

This is why Phase 5 has a 30-day quarantine.

## Phase ordering rationale

Why this order:
- **Phase 1 is additive** — zero risk to running system.
- **Phase 2 changes lake-side writes only** — live API unaffected.
- **Phase 3 changes live behavior** — risky, but the only risky phase.
- **Phase 4 is additive again** — adds a new endpoint.
- **Phase 5 is destructive but quarantined** — 30 days to catch any
  bug we missed before deletes are irreversible.

## Total effort

| Phase | Implementation time | Wall-clock for the job itself |
|---|---|---|
| Phase 1 | ~4 hours of code | ~2 hours for polygon_adjustment_job whole-market |
| Phase 2 | ~1 hour of code | live (no batch) |
| Phase 3 | ~30 min of code | live + 30s for gate test |
| Phase 4 | ~2 hours of code | n/a |
| Phase 5 | ~30 min of code (30 day quarantine) | seconds (drops) |

**Total active engineering: ~8 hours.** Plus 30 days observation
window before Phase 5.

## Migration gotchas

| Gotcha | Mitigation |
|---|---|
| Glue catalog quota | Default is 1000 partitions/table — well under our 32 buckets × 60 months = 1920. **Action**: request a quota bump to 10,000 before Phase 1. |
| Iceberg writer Java heap | Spark writes with many partitions need 4-8 GB driver memory. **Action**: configure EMR Serverless driver workerConfig to `8 vCPU / 32 GB` if OOM. |
| Schwab token expires during cutover | OAuth refresh tokens expire after ~7 days. **Action**: refresh token + restart uvicorn 24h before Phase 3 cutover. |
| Live ticks during Phase 2 transition | live_lake_writer writes to BOTH bronze.schwab_minute AND data.schwab_universe during the transition window. Double-storage temporary. **Action**: after CV7 verified, delete bronze.schwab_minute writes (Phase 5). |
| Phase 3 gate fail | If the latency gate fails at <5s after cutover, the rollback is one git revert. **Action**: have CV8 ready to revert; don't push CV9-10 until gate passes. |

## Pre-Phase 1 checklist

- [ ] [08_decisions.md](08_decisions.md) gates 1-7 approved
- [ ] `STOCK_LAKE_BUCKET` env set in production
- [ ] EMR Serverless app created (one-time `aws emr-serverless create-application`)
- [ ] `scripts/spark/__init__.py` + `polygon_adjustment_job.py` files reviewed for production-readiness
- [ ] Disaster recovery test: can we restore CH from `data.schwab_universe` snapshot? (dry-run script)

## See also

- [01_architecture.md](01_architecture.md) — system context
- [02_schema.md](02_schema.md) — Iceberg DDL referenced in commits
- [04_spark.md](04_spark.md) — `polygon_adjustment_job` implementation
- [07_runbook.md](07_runbook.md) — operator procedures (restart, refresh tokens, etc.)
- [08_decisions.md](08_decisions.md) — approval gates blocking Phase 1
