# 08 — Decisions (Approved)

All 14 gates approved 2026-05-20. This file is the durable record of
**what was decided** + **why** + **what would force a revisit**.

Cross-file references: when a downstream doc says "v2 namespace is
`equities`" or "weekly compaction", the source of truth is here.

## Gate 1 — Iceberg database name

**Decision:** `equities`. Tables are `lake.equities.<table>`.

**Rationale:** Leaves room for `lake.crypto.*`, `lake.fx.*`, etc.
without renaming or carving sub-prefixes inside `data`. The Spark
catalog stays fixed at `lake` (configured in `get_spark()`).

**Revisit trigger:** If we ever decide one database per
asset-class is too granular and want a single `data` catalog,
Iceberg lets us rename a database without rewriting files.

## Gate 2 — `adj_factor` column on adjusted tables

**Decision:** YES. Include `adj_factor DOUBLE NOT NULL DEFAULT 1.0`
on `lake.equities.polygon_adjusted` and `lake.equities.schwab_universe`.

**Rationale:** ML features that depend on price level need the factor
to back-compute raw prices (`raw = adjusted * adj_factor`). Schwab's
pass-through gets `1.0` (no info loss). Storage cost is trivial
(~5 GB zstd-compressed over the full 5y whole-market).

**Revisit trigger:** Never — this is a permanent schema commitment.

## Gate 3 — Partition strategy

**Decision:**

| Table | Partition spec |
|---|---|
| `lake.equities.polygon_raw` | `bucket(32, symbol), month(timestamp)` |
| `lake.equities.polygon_adjusted` | `bucket(32, symbol), month(timestamp)` |
| `lake.equities.schwab_universe` | `bucket(16, symbol), month(timestamp)` |
| `lake.equities.market_corp_actions` | `month(ex_date)` only |

**Rationale:** Target ~5-10 symbols per bucket so per-bucket files
hit Iceberg's ~128 MB sweet spot. 32 for ~12k Polygon symbols, 16
for the smaller ~108-symbol Schwab universe.

**Revisit trigger:** If symbol count exceeds 50k (e.g. expand into
small-caps), partition evolution to `bucket(64, symbol)` without
data migration.

## Gate 4 — Compaction cadence

**Decision:**

| Table | Cadence | Reason |
|---|---|---|
| `lake.equities.schwab_universe` | Weekly | Hourly archive job → small files |
| `lake.equities.polygon_adjusted` | Monthly | Only rewrites on corp-action churn |
| `lake.equities.polygon_raw` | Monthly | Mostly static |
| `lake.equities.market_corp_actions` | On-demand | Tiny table |

**Rationale:** Schwab archive writes ~24 small Parquet files/day; if
left uncompacted, single-symbol scans degrade within ~6 weeks. Polygon
tables don't churn enough to justify weekly.

**Revisit trigger:** If `aws s3 ls --recursive | wc -l` for any single
table exceeds 10,000 files, bump that table's cadence one step.

## Gate 5 — Spark compute platform

**Decision:** Local PySpark for everything routine. EMR Serverless as
the on-demand AWS escape hatch — launcher scripts ship in Phase 1 (CV4)
so they're ready, but jobs run local by default.

**Rationale:** EMR Serverless is pay-per-job ($0 idle), which exactly
matches "use AWS when I need it." Tree-model training and per-symbol
adjustments fit in a 16 GB dev box; the EMR option exists for the
weekly whole-market `polygon_adjustment_job` if local can't keep up.

**Revisit trigger:** If a local Spark job exceeds 30-min wall-clock on
a 16 GB box, switch that job's runner to EMR Serverless. No code
change — just submit the same `scripts/spark/*.py` entry point via
`aws emr-serverless start-job-run`.

## Gate 6 — Migration plan + risk tolerance

**Decision:** 5-phase plan as written in [`06_migration.md`](06_migration.md),
**but compress Phase 5 quarantine from 30 days to 7 days.** Legacy
`lake.bronze.*` and `lake.silver.*` tables drop one week after the
Phase 3 cutover.

**Rationale:** Faster cleanup; recovers ~$5/mo dual-storage cost
sooner; reduces operator cognitive load (two source-of-truth tables
for the same data is confusing).

**Watch-out (accepted):** A latent v2 bug that surfaces past day 7
forces a re-bulk-load from Polygon flat-files (~6h, ~$15). Acceptable
because (a) Phase 3 is the only behavior change and is testable in
under an hour, (b) flat-files in `s3://stockalert-lake/raw/polygon/`
are immutable so the re-load is mechanical.

**Revisit trigger:** If Phase 3 cutover shows ANY regression in the
first 48 hours, hold legacy tables until the regression is root-caused
and fixed. The 7-day clock restarts.

## Gate 7 — `/api/v1/lake/bars` endpoint + MCP tool surface

**Decision:** Build the FastAPI endpoint in Phase 4 (CV11+CV12).
**Additionally**, ship MCP tool wrappers in CV12b so the assistant
agent can query deep history via the same code path.

**Rationale:** Two consumers of the same SQL: cockpit chart zoom-out
and agent research workflows. One implementation, both surfaces.

**MCP tools shipped (`app/mcp/tools.py`):**
- `lake_bars(symbol, start, end, timeframe)` — single-symbol UNION across
  `polygon_adjusted` + `schwab_universe`
- `lake_cross_provider_diff(symbol, start, end)` — provider-quality probe
  (Pattern 6 from [`04_spark.md`](04_spark.md))
- `lake_snapshot_list(table)` — exposes snapshot IDs for time-travel queries

**Revisit trigger:** If the MCP tools become a security concern (S3
read costs from agent-driven workloads exceeding $10/mo), add a
per-tool rate limit. Endpoint stays.

## Gate 8 — ML training compute

**Decision:** SageMaker for production retraining; local Python for
experiments. Artifacts always land at `s3://stockalert-models/<name>/<ver>/`.

**Rationale:** SageMaker training jobs are managed, queue-able, and
their stdout/stderr persists by default — useful for the weekly cron.
Local Python stays viable for ad-hoc experiments. Spark is the wrong
tool for tree-model training (single-box is faster).

**Revisit trigger:** If model wall-clock on `ml.m5.4xlarge` exceeds
2 hours, move to `ml.m5.12xlarge` or larger. If we ever train deep
sequence models (LSTM/Transformer on bars), switch to a GPU instance
(`ml.g5.xlarge`).

## Gate 9 — Universe history source

**Decision:** Polygon `/v3/reference/tickers` historical endpoint.
Daily snapshot writes one row per (date, symbol) to
`lake.equities.point_in_time_universe`.

**Rationale:** Already on the Polygon subscription. Includes
delistings + reasons (`merged`, `bankrupt`, `acquired`). Eliminates
survivorship bias from every backtest. Daily ingest is ~5 MB.

**Revisit trigger:** If Polygon subscription ends, fall back to
Alpha Vantage (free tier covers ~20y of historical tickers) or
hand-maintained CSV.

## Gate 10 — Model artifact format

**Decision:** Library-native binary. Specifics by library:

| Library | Format | Loader |
|---|---|---|
| XGBoost | `.ubj` (UBJSON) | `model.save_model()` / `model.load_model()` |
| LightGBM | `.txt` | `lgb.Booster.save_model()` / `lgb.Booster(model_file=...)` |
| sklearn estimators | joblib + library version pin | `joblib.dump` / `joblib.load` |

`model_registry.hyperparams_json` records the **exact library version**
used at save time. Loaders check version compatibility on read.

**Rationale:** Native formats are stable within a library's
compatibility range, fast to load (~50ms), and don't have ONNX
conversion bugs. Pickle is explicitly ruled out — fragile across
Python minor versions.

**Revisit trigger:** If we ever serve from a non-Python runtime
(Rust/C++ for sub-ms inference), revisit ONNX conversion for the
production model only.

## Gate 11 — Canary traffic split

**Decision:** Fixed 10% of live inferences for 14 days. Manual
promotion review at day 14.

**Promotion criteria (all three must hold):**
1. Canary live-PnL ≥ challenger backtest expectation
2. No PnL regressions vs prod on the same symbols
3. Drift monitors stay `severity ∈ {ok, warn}` (no `alert`)

**Rationale:** Simple state machine, clear evaluation window, manual
review keeps the human in the loop for the highest-risk decision.
Gradual ramp adds state complexity for marginal benefit.

**Revisit trigger:** If we run >10 challenger evaluations and >9 pass
the gate on the first try, switch to gradual ramp with auto-promote.

## Gate 12 — Initial backfill scope

**Decision:** Full 5-year Polygon bulk-load for the entire IEX
universe (~11k symbols) on day 1 of Phase 2. Populates both
`lake.equities.polygon_raw` (re-bucketed from existing v1
`bronze.polygon_minute`) and `lake.equities.polygon_adjusted` (via the
one-time `polygon_adjustment_job` whole-market run).

**Cost:** ~$15 CodeBuild + ~$8 S3 PUT + ~$5 S3 Standard / month
ongoing. Wall-clock ~6 hours.

**Rationale:** Models training in Phase 2.5 need max history available
immediately; lazy backfill delays ML cold-start by 3-6 weeks.

**Revisit trigger:** If the operator wants to constrain to a
sub-universe later (e.g. only S&P 1500), Iceberg's `DELETE FROM`
on a symbol predicate removes data cleanly without touching the
schema or other symbols.

## Gate 13 — Schwab universe seed

**Decision:** Top-250 by 30-day average dollar volume from Polygon.
Weekly rebalance writes to `stream_universe`. Includes major ETFs
naturally (SPY, QQQ, etc. are among the highest-volume tickers).

**Rationale:** Automated, transparent, no operator maintenance.
Selects most liquid names → lowest slippage in sim and live. Beats
S&P 500 (no ETFs) and manual curation (drift-prone).

**Revisit trigger:** If a non-ADV-ranked symbol becomes critical
(e.g. specific sector ETF for hedging research), operator manual
override via `POST /api/v1/stream`. Rebalance respects manual adds.

## Gate 14 — ClickHouse `bars_silver` schema

**Decision:** Adjusted-only (unchanged from v1). Cockpit charts
read adjusted bars; raw bars stay lake-only.

**Rationale:** Cockpit has no need for raw bars today; adding
`bars_silver_raw` doubles CH storage for a feature nobody's
asking for. If a future UI feature needs split-aware historical
display (e.g. "show me the actual pre-split prices on the chart"),
add the second table then.

**Revisit trigger:** New UI feature explicitly requires pre-split
prices in the chart, or a regulatory requirement to display raw
prices alongside adjusted.

## Watch-out items (non-default decisions)

Three decisions diverge from the original recommendations. Flagged
here so future operators know the rationale wasn't accidental:

| Gate | Default was | Decision was | Why it matters |
|---|---|---|---|
| 1 | `data` | `equities` | All FQNs are `lake.equities.*`; namespace exists per asset class |
| 6 | 30-day quarantine | 7-day quarantine | Faster cleanup; latent-bug fallback is re-bulk-load not partial revert |
| 7 | Endpoint only | Endpoint + MCP tools | Adds CV12b commit; agent surface needs auth boundary check before merge |

## Decision log

| Date | Gates | Decision | Approver |
|---|---|---|---|
| 2026-05-20 | 1-14 | All approved (see per-gate sections above). Defaults except 1, 6, 7. | operator |

This table is append-only. Any future revisit (e.g. revisit-trigger
fires) adds a new row with the revised decision + reasoning, keeping
the original row intact for audit.

## See also

- [01_architecture.md](01_architecture.md) — system layout these gates configure
- [06_migration.md](06_migration.md) — the phases these gates unblock
- [10_ml_pipeline.md](10_ml_pipeline.md) — ML pipeline gates 8-11 configure
