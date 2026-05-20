# 08 — Open Decisions (Approval Gates)

These need operator sign-off before Phase 1 implementation starts.
Each has a recommended default; amend if needed.

Gates 1-7 cover the lake/Spark infrastructure (Phase 1 prerequisites).
Gates 8-12 cover the ML pipeline (Phase 2.5 — land after `data.polygon_adjusted`
exists but before the first model trains); see [10_ml_pipeline.md](10_ml_pipeline.md).

## Gate 1 — Table naming

**Question:** What's the Iceberg **database** name inside the `lake`
catalog? Tables are referenced as `lake.<database>.<table>`.

| Option | Database name | Table refs |
|---|---|---|
| **Recommended** | `data` | `lake.data.polygon_raw`, `lake.data.polygon_adjusted`, ... |
| `lake` | `lake.lake.polygon_raw` (redundant) |
| `equities` | `lake.equities.polygon_raw` |
| `prod` | `lake.prod.polygon_raw` |

The Spark catalog is fixed at `lake` (configured in `get_spark()` via
`spark.sql.catalog.lake`). Only the database-name level is in scope here.

**My pick:** `data` — short, neutral, matches the "data lake" mental model.

**Status:** ☐ Pending

## Gate 2 — `adj_factor` column on adjusted tables

**Question:** Include `adj_factor DOUBLE NOT NULL DEFAULT 1.0` in
`data.polygon_adjusted` and `data.schwab_universe`?

**Why include:**
- ML feature engineering can back-compute raw prices when needed:
  `raw = adjusted * adj_factor`.
- Audit trail — you can see "this bar was adjusted by factor 4.0"
  (i.e. there's a 4-for-1 split somewhere after this bar).
- Schwab's pass-through gets `1.0` (no info loss vs adding it later).

**Cost:** One DOUBLE column = ~8 bytes per row. Whole-market 5y
= ~40 GB extra. With zstd compression: ~5 GB extra. Trivial.

**My pick:** YES, include.

**Status:** ☐ Pending

## Gate 3 — Partition strategy

**Question:** Confirm `bucket(N, symbol), month(timestamp)` on the
adjusted/raw tables.

- `data.polygon_raw`: `bucket(32, symbol), month(timestamp)`
- `data.polygon_adjusted`: same
- `data.schwab_universe`: `bucket(16, symbol), month(timestamp)`
  (smaller N because the universe is smaller)
- `data.market_corp_actions`: `month(ex_date)` only — no symbol bucket

**Tradeoffs:**
- Larger N → more files per month → finer single-symbol skipping but
  Iceberg metadata overhead grows. 32 is the sweet spot for 12k symbols.
- Smaller N → fewer, larger files → faster whole-market scans but
  slower per-symbol. 16 is fine for the ~108-symbol universe.

**My pick:** YES, as specified.

**Status:** ☐ Pending

## Gate 4 — Compaction cadence

**Question:** How often do we run Iceberg file compaction?

| Schedule | Cost | When |
|---|---|---|
| Daily | ~$1/day = $30/month | If lake_archive_job runs hourly and creates many small files |
| **Weekly** (recommended) | ~$1/week = $4/month | Sunday morning, before Monday's reads |
| Monthly | ~$1/month | Slow growth datasets |

For:
- `data.schwab_universe`: **weekly** (grows hourly, lots of small files)
- `data.polygon_adjusted`: **monthly** (mostly static, only corp-action rewrites)
- `data.polygon_raw`: **monthly** (frozen + occasional nightly)
- `data.market_corp_actions`: **on-demand only** (small)

**My pick:** Mixed cadence as above; run via EMR Serverless cron.

**Status:** ☐ Pending

## Gate 5 — Compute platform

**Question:** Where do batch Spark jobs run?

| Option | Setup | Cost | When |
|---|---|---|---|
| **EMR Serverless** (recommended) | One-time `create-application` | Pay-per-job, ~$0.30/hr DBU | Production batch (weekly cron) |
| Local Spark | `pip install pyspark` | $0 | Dev / one-shot operator runs |
| EMR on EC2 | Manual cluster lifecycle | EC2 hourly | Heavy ongoing ETL (not needed at our scale) |
| Databricks | Subscription | Per-DBU | If team already has Databricks |

**My pick:** Local Spark for dev + EMR Serverless for production
cron. No standing infrastructure, no DBA work.

**Status:** ☐ Pending

## Gate 6 — Migration risk tolerance

**Question:** Are you comfortable with the 5-phase migration plan as
written?

- Phase 1 (additive Iceberg tables): zero risk to live tier.
- Phase 2 (lake writers redirect): zero risk to live tier; lake is
  dual-written during transition.
- **Phase 3 (live tier cuts over)**: the only point of behavior
  change. One-line revert if the latency gate fails.
- Phase 4 (lake-read endpoint): additive only.
- Phase 5 (drop legacy tables): destructive. **30-day quarantine**
  after Phase 3 before this runs.

Total active engineering: ~8 hours of code; ~3 hours of Spark
wall-clock during Phase 1; 30 days of observation before Phase 5.

**My pick:** YES — this is the right risk profile. Each phase
independently reversible; the only destructive phase is gated by a
month of clean v2 operation.

**Status:** ☐ Pending

## Gate 7 — `/api/v1/lake/bars` endpoint

**Question:** Build the lake-read FastAPI endpoint in Phase 4, or
keep deep-history queries operator-only via DuckDB CLI?

**Build it (Phase 4):**
- Pro: cockpit's chart can zoom out beyond CH retention seamlessly
- Pro: agents / MCP tools can query deep history via HTTP
- Con: ~2 hours of code; DuckDB-on-S3 has cold-start latency

**Keep operator-only:**
- Pro: zero code; just `duckdb` from a shell
- Con: chart can't show 5y of 1-min for new symbols (today's design has this gap anyway)

**My pick:** Build it (Phase 4). The cockpit will need it for chart
zoom-out beyond CH retention as soon as anyone scrolls back >1 year
at 1-min resolution.

**Status:** ☐ Pending

## Gate 8 — ML training compute

**Question:** Where do training jobs run?

| Option | Setup | Cost | Notes |
|---|---|---|---|
| **SageMaker training jobs** (recommended) | IAM role + S3 bucket | ~$5/run on ml.m5.4xlarge | Managed, queue-able, logged; ad-hoc experiments still work locally |
| Local Python | Dev box | $0 | No artifact persistence beyond manual S3 upload |
| EMR Serverless | Existing app | ~$2-3 | Spark is the wrong tool for tree-model training (single-box is faster) |

**My pick:** SageMaker for production retraining; local for experiments.

**Status:** ☐ Pending

## Gate 9 — Universe history source

**Question:** Where does `data.point_in_time_universe` come from?

| Option | Cost | Coverage |
|---|---|---|
| **Polygon historical tickers endpoint** (recommended) | included in current Polygon sub | full delistings + reasons |
| Manually-curated CSV | $0 | becomes stale; bias risk |
| Alpha Vantage / IEX | $$ | uncertain coverage of delisted names |

**My pick:** Polygon — already on subscription; daily refresh.

**Status:** ☐ Pending

## Gate 10 — Model artifact format

**Question:** How are model artifacts persisted in `s3://stockalert-models/`?

| Option | Portability | Fragility |
|---|---|---|
| **Library-native binary** (XGBoost `.json`/`.ubj`, LightGBM `.txt`) (recommended) | high (within library version range) | low |
| Pickle | Python-only | high — breaks across library/Python versions |
| ONNX | high (cross-framework) | tree models lose performance; conversion bugs |

**My pick:** Library-native binary; document the library version in `model_registry.hyperparams_json`.

**Status:** ☐ Pending

## Gate 11 — Canary traffic split

**Question:** How does a `canary` model take live traffic?

| Option | Pro | Con |
|---|---|---|
| **Fixed 10% for 14 days** (recommended) | Simple; clear evaluation window | Slow rollout if confidence is high |
| Gradual ramp (10% → 25% → 50% → 100%) | Faster rollout when behaving | More state to manage; harder to attribute regressions |
| Shadow (100% predictions, 0% trades) | Risk-free | No PnL signal — defeats the canary point |

**My pick:** Fixed 10% for 14 days, manual promotion after PnL gate.

**Status:** ☐ Pending

## Gate 12 — Drift alert thresholds

**Question:** What KS-statistic triggers `drift_severity = 'alert'`?

| Option | Sensitivity | False-alarm rate |
|---|---|---|
| `> 0.10` | High | High — every regime shift trips it |
| **`> 0.15` with 3-consecutive-day smoothing** (recommended) | Medium | Low — survives one-day noise |
| `> 0.20` | Low | Very low — may miss real drift |
| Per-feature thresholds | Tuned | Maintenance burden |

**My pick:** 0.15 + 3-day smoothing; per-feature overrides as needed later.

**Status:** ☐ Pending

## How to approve

Reply with one of:

- **"All defaults"** — accepts all 12 recommendations as written.
- **"Approved with changes: [gate N: change Y]"** — selectively
  amend.
- **"More questions on [gate N]"** — pause for discussion.

Once approved, Phase 1 commit (`CV1`) lands in the next session.

## Decision log (post-approval)

| Date | Gate | Decision | Approver |
|---|---|---|---|
| (pending) | 1-7 | (pending) | (pending) |

This table gets updated as decisions land so the audit trail is
clear if anything is revisited in 6 months.

## See also

- [01_architecture.md](01_architecture.md) — what these gates affect
- [06_migration.md](06_migration.md) — the phases blocked by approval
