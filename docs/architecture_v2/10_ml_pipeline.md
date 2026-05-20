# 10 — ML Pipeline

How models are trained, evaluated, deployed, and monitored on the v2
lake + live stack. Builds on [`02_schema.md`](02_schema.md) (canonical
OHLCV columns) and [`04_spark.md`](04_spark.md) (Spark patterns).

## TL;DR

```
   data.polygon_adjusted ─┐
   data.schwab_universe   ├─► FEATURE JOB (Spark, weekly + ad-hoc)
   data.market_corp_actions┘            │
                                        ▼
                              data.features_1m_v{N}   ← Iceberg, snapshot-pinned
                                        │
                                        ▼
                              LABEL JOB (Spark)
                                        │
                                        ▼
                              data.labels_v{N}
                                        │
   data.point_in_time_universe ────────►├─►  TRAIN JOB (SageMaker / local)
                                        │       │
                                        │       ▼
                                        │   s3://stockalert-models/<name>/<ver>/
                                        │   + data.model_registry
                                        │       │
                            ┌───────────┴───────┴──────────────┐
                            ▼                                  ▼
                       BACKTEST (sim/)                    LIVE INFERENCE
                       reads Iceberg snap                 reads CH.ohlcv_1m
                       writes sim_runs                    writes signals
                                                                │
                                                                ▼
                                                         MONITORING
                                                         (drift, calibration, PnL)
```

Five new Iceberg tables, one shared Python feature module, snapshot
pinning end-to-end.

## Design principles

1. **Lake is ground truth for training.** Snapshot-pinned Iceberg
   tables guarantee a model can be retrained on byte-identical data
   months later.
2. **Train/serve parity is a hard rule.** The exact same Python
   function computes features for Spark batch and live inference. No
   reimplementations.
3. **Walk-forward only.** Random train/test splits leak future
   information through indicators with lookback windows. Use
   chronological splits with embargo periods (Lopez de Prado).
4. **Point-in-time universe.** Train on the universe as it existed
   historically, not today's universe. Otherwise survivorship bias
   silently inflates backtest PnL.
5. **Reproducibility metadata is mandatory.** Every model artifact
   records the Iceberg `snapshot_id` of each input table. A pipeline
   that can't be reproduced is a science experiment, not a product.

## New Iceberg tables

### `data.features_1m_v{N}`

One row per `(symbol, timestamp)` with all engineered features for
that bar. `v{N}` is the feature-set version — never mutate; bump
version for any schema change. Old versions stay queryable.

```sql
CREATE TABLE lake.data.features_1m_v1 (
    symbol                STRING NOT NULL,
    timestamp             TIMESTAMP NOT NULL,

    -- Raw bar (carried forward for joins / sanity)
    close                 DOUBLE NOT NULL,
    volume                DOUBLE NOT NULL,

    -- Returns
    log_ret_1m            DOUBLE,
    log_ret_5m            DOUBLE,
    log_ret_30m           DOUBLE,
    log_ret_1h            DOUBLE,

    -- Volatility
    realized_vol_30m      DOUBLE,
    realized_vol_1h       DOUBLE,
    realized_vol_1d       DOUBLE,

    -- Technicals (mirrors app/indicators/)
    rsi_14                DOUBLE,
    macd                  DOUBLE,
    macd_signal           DOUBLE,
    ema_20                DOUBLE,
    ema_50                DOUBLE,
    ema_200               DOUBLE,

    -- Microstructure
    volume_zscore_30m     DOUBLE,
    vwap_dist_bps         DOUBLE,
    spread_bps            DOUBLE,        -- nullable; only available on quote-bearing sources

    -- Provenance
    feature_set_version   STRING NOT NULL DEFAULT 'v1',
    source_snapshot_id    BIGINT NOT NULL    -- snapshot of polygon_adjusted used
)
PARTITIONED BY (
    bucket(32, symbol),
    month(timestamp)
)
TBLPROPERTIES (
    'format-version' = '2',
    'write.parquet.compression-codec' = 'zstd',
    'write.distribution-mode' = 'hash'
);
```

Expected size: ~12k symbols × 5y × ~390 bars/day × 252 trading days
≈ 5B rows × ~150 bytes/row uncompressed → **~50 GB zstd-compressed
per feature-set version**.

### `data.labels_v{N}`

Separate from features so the same features can be tested against
multiple label definitions (forward returns at different horizons,
triple-barrier with different barrier widths, etc.).

```sql
CREATE TABLE lake.data.labels_v1_fwd5m (
    symbol               STRING NOT NULL,
    timestamp            TIMESTAMP NOT NULL,
    fwd_log_ret_5m       DOUBLE NOT NULL,        -- regression target
    fwd_class_5m         INT,                    -- -1/0/+1 if classifier
    label_version        STRING NOT NULL DEFAULT 'v1_fwd5m',
    source_snapshot_id   BIGINT NOT NULL
)
PARTITIONED BY (bucket(32, symbol), month(timestamp))
TBLPROPERTIES ('format-version' = '2');
```

Naming convention: `labels_v{N}_{type}{horizon}`. Examples:
`labels_v1_fwd5m`, `labels_v1_triple_2sigma_60m`, `labels_v2_max_dd_1d`.

### `data.point_in_time_universe`

The universe membership history. One row per `(date, symbol)` for
every date a symbol was investable. Used by the training job to
filter out symbols that didn't yet exist (or had been delisted) at
the historical training timestamp.

```sql
CREATE TABLE lake.data.point_in_time_universe (
    date           DATE NOT NULL,
    symbol         STRING NOT NULL,
    in_universe    BOOLEAN NOT NULL,
    delisted_at    DATE,                   -- null if still listed
    universe_tag   STRING NOT NULL,        -- 'sp500', 'russell3000', 'investable_top_5000'
    reason         STRING                  -- 'new_listing', 'merged', 'bankrupt', 'price<5', etc.
)
PARTITIONED BY (year(date))
TBLPROPERTIES ('format-version' = '2');
```

Built by a separate ingest from a universe-history provider (Polygon
historical tickers endpoint, or a manually-curated CSV for the
starting cut). Updated daily.

**Without this table, every backtest has survivorship bias.** Today's
universe excludes everything that died; training on today's universe
trains on winners only.

### `data.model_registry`

One row per `(name, version)` model. The S3 path holds the artifact;
this table holds the metadata.

```sql
CREATE TABLE lake.data.model_registry (
    name                       STRING NOT NULL,        -- 'swing_5m_xgb'
    version                    STRING NOT NULL,        -- '2025-05-20-a3f1'
    s3_uri                     STRING NOT NULL,        -- model.pkl location
    status                     STRING NOT NULL,        -- 'training' | 'canary' | 'prod' | 'retired'

    -- Training data lineage
    feature_set_version        STRING NOT NULL,
    label_version              STRING NOT NULL,
    features_snapshot_id       BIGINT NOT NULL,
    labels_snapshot_id         BIGINT NOT NULL,
    universe_snapshot_id       BIGINT NOT NULL,

    -- Training config
    train_start                DATE NOT NULL,
    train_end                  DATE NOT NULL,
    walk_forward_n_splits      INT NOT NULL,
    embargo_days               INT NOT NULL,
    hyperparams_json           STRING NOT NULL,

    -- Evaluation
    cv_sharpe_mean             DOUBLE,
    cv_sharpe_std              DOUBLE,
    holdout_sharpe             DOUBLE,
    holdout_max_drawdown       DOUBLE,
    holdout_trades_per_day     DOUBLE,

    -- Lifecycle
    trained_at                 TIMESTAMP NOT NULL,
    promoted_at                TIMESTAMP,
    retired_at                 TIMESTAMP
)
PARTITIONED BY (name)
TBLPROPERTIES ('format-version' = '2', 'write.upsert.mode' = 'merge-on-read');
```

### `data.feature_drift_metrics`

Daily drift measurements per (feature, symbol-bucket). Lets you
detect when the live distribution diverges from training.

```sql
CREATE TABLE lake.data.feature_drift_metrics (
    date              DATE NOT NULL,
    feature_name      STRING NOT NULL,
    symbol_bucket     INT NOT NULL,           -- bucketed for per-segment monitoring
    train_mean        DOUBLE NOT NULL,
    train_std         DOUBLE NOT NULL,
    live_mean         DOUBLE NOT NULL,
    live_std          DOUBLE NOT NULL,
    ks_statistic      DOUBLE NOT NULL,
    ks_pvalue         DOUBLE NOT NULL,
    drift_severity    STRING NOT NULL         -- 'ok' | 'warn' | 'alert'
)
PARTITIONED BY (month(date))
TBLPROPERTIES ('format-version' = '2');
```

## Stage-by-stage walkthrough

### Stage 1 — Feature engineering

| | |
|---|---|
| **Code** | `app/ml/features/v1.py` (the shared module) + `scripts/spark/feature_build_v1.py` (the Spark driver) |
| **Reads** | `lake.data.polygon_adjusted`, `lake.data.schwab_universe` (UNION via the cross-provider pattern from `02_schema.md`) |
| **Writes** | `lake.data.features_1m_v1` |
| **Where** | EMR Serverless, weekly cron + on-demand for backfills |
| **Cost** | ~$3 per whole-market run, ~$0.10 per single-symbol incremental |
| **Cadence** | Sunday 07:00 UTC (after `polygon_adjustment_job` completes at 06:00 UTC) |

The shared feature module:

```python
# app/ml/features/v1.py
"""Feature set v1 — used by Spark batch AND live inference.

This is the SINGLE source of truth. Changing this module means
training data drifts from inference data, which silently breaks
every model. To change features, create v2.py — never mutate v1.
"""
import pandas as pd
import numpy as np

FEATURE_NAMES = [
    "log_ret_1m", "log_ret_5m", "log_ret_30m", "log_ret_1h",
    "realized_vol_30m", "realized_vol_1h", "realized_vol_1d",
    "rsi_14", "macd", "macd_signal", "ema_20", "ema_50", "ema_200",
    "volume_zscore_30m", "vwap_dist_bps", "spread_bps",
]

def compute(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute v1 features.

    Input: bars sorted by (symbol, timestamp) with columns
    [symbol, timestamp, open, high, low, close, volume, vwap].

    Output: same index, FEATURE_NAMES columns.

    MUST be deterministic. MUST not look at future data. MUST handle
    NaN tails (first N rows of each symbol where window isn't full).
    """
    g = bars.groupby("symbol", group_keys=False)
    out = pd.DataFrame(index=bars.index)
    out["log_ret_1m"] = g["close"].apply(lambda s: np.log(s / s.shift(1)))
    out["log_ret_5m"] = g["close"].apply(lambda s: np.log(s / s.shift(5)))
    # ... etc
    return out
```

The Spark driver applies `compute()` per partition (group by symbol)
using a pandas UDF, then writes:

```python
# scripts/spark/feature_build_v1.py
from pyspark.sql.functions import pandas_udf
from app.ml.features.v1 import compute, FEATURE_NAMES

@pandas_udf("symbol string, timestamp timestamp, " +
            ", ".join(f"{n} double" for n in FEATURE_NAMES))
def features_udf(pdf):
    return compute(pdf)

bars = spark.sql("""
    SELECT symbol, timestamp, open, high, low, close, volume, vwap
    FROM lake.data.polygon_adjusted
    WHERE timestamp >= TIMESTAMP '2018-01-01'
""")

snapshot_id = spark.sql(
    "SELECT max(snapshot_id) FROM lake.data.polygon_adjusted.snapshots"
).collect()[0][0]

features = (bars.groupBy("symbol")
                .applyInPandas(features_udf, schema=...)
                .withColumn("source_snapshot_id", F.lit(snapshot_id))
                .withColumn("feature_set_version", F.lit("v1")))

features.writeTo("lake.data.features_1m_v1").overwritePartitions()
```

### Stage 2 — Labeling

| | |
|---|---|
| **Code** | `scripts/spark/label_build_v1_fwd5m.py` |
| **Reads** | `lake.data.polygon_adjusted` (for forward returns) |
| **Writes** | `lake.data.labels_v1_fwd5m` |
| **Where** | EMR Serverless, runs after feature build |
| **Cost** | ~$1 per whole-market run |

Two common label types for day/swing trading:

**Forward return (regression):**
```sql
SELECT symbol, timestamp,
       LN(LEAD(close, 5) OVER (PARTITION BY symbol ORDER BY timestamp)
          / close) AS fwd_log_ret_5m
FROM lake.data.polygon_adjusted
```

**Triple-barrier (classification, Lopez de Prado §3):**
At each bar `t`, look forward H minutes; was the upper barrier (e.g.
`+2 × realized_vol`) hit first? Lower? Timeout? → label ∈ {+1, -1, 0}.

```python
# scripts/spark/label_build_v1_triple_2sigma_60m.py
@pandas_udf(...)
def triple_barrier_udf(pdf):
    # For each row, walk forward up to 60 bars
    # Return +1 if upper hit first, -1 if lower hit first, 0 if timeout
    ...
```

### Stage 3 — Training

| | |
|---|---|
| **Code** | `scripts/ml/train_v1.py` |
| **Reads** | `lake.data.features_1m_v1`, `lake.data.labels_v1_fwd5m`, `lake.data.point_in_time_universe` |
| **Writes** | `s3://stockalert-models/<name>/<version>/` + `lake.data.model_registry` |
| **Where** | SageMaker training job OR local Python on a 32-GB box |
| **Cost** | ~$5 per whole-market XGBoost run (~30 min on ml.m5.4xlarge) |
| **Cadence** | Weekly, or on-demand for experiments |

**Spark is NOT used for training.** Tree models train faster on a
single beefy box with the dataset in RAM. Use Spark to **materialize
the training set as Parquet**, then load it into pandas/numpy and
train locally or on SageMaker.

```python
# scripts/ml/train_v1.py
import xgboost as xgb
from app.ml.snapshots import pin_current_snapshots, write_registry_row
from app.ml.cv import walk_forward_splits

# 1. Pin Iceberg snapshots so this run is reproducible
snaps = pin_current_snapshots([
    "lake.data.features_1m_v1",
    "lake.data.labels_v1_fwd5m",
    "lake.data.point_in_time_universe",
])

# 2. Materialize training set via Spark → Parquet on S3
training_s3_uri = build_training_set(
    snaps,
    universe_tag="investable_top_5000",
    train_start="2020-01-01",
    train_end="2024-06-30",
)

# 3. Load → train
df = pd.read_parquet(training_s3_uri)
X, y = df[FEATURE_NAMES], df["fwd_log_ret_5m"]

# 4. Walk-forward CV (chronological, no shuffle, with embargo)
splits = walk_forward_splits(df["timestamp"], n_splits=5, embargo="1d")

model = xgb.XGBRegressor(n_estimators=500, max_depth=6, ...)
cv_metrics = cross_validate(model, X, y, cv=splits, scoring=["neg_mse", sharpe_scorer])

# 5. Fit on full train range, evaluate on hold-out (2024-07 onward)
model.fit(X, y)

# 6. Persist artifact + registry row
version = save_model(
    name="swing_5m_xgb",
    model=model,
    s3_uri_base="s3://stockalert-models/",
    snapshots=snaps,
    cv_metrics=cv_metrics,
    train_start="2020-01-01",
    train_end="2024-06-30",
)
write_registry_row(name="swing_5m_xgb", version=version, status="training", ...)
```

### Stage 4 — Backtest

| | |
|---|---|
| **Code** | `app/services/sim/` (existing module, ported to v2) |
| **Reads** | Pinned snapshots from `model_registry`, plus `polygon_adjusted` + `schwab_universe` UNION for the hold-out window |
| **Writes** | `lake.data.sim_runs`, `lake.data.sim_trades` (Iceberg) |
| **Where** | Local Python or EMR Serverless, depending on scope |

Walk-forward backtest IS the truth signal. Cross-validation is
gameable; an honest out-of-sample PnL curve isn't.

For each hold-out bar:
1. Load features (from `features_1m_v1` at the pinned snapshot)
2. Score with the model
3. Apply trade rules (entry threshold, position sizing, exit logic)
4. Simulate fills, slippage, fees
5. Record trade and running PnL

Promotion gate to canary: hold-out Sharpe > prod Sharpe + 0.5,
max drawdown < 15%, trades-per-day in [0.5, 5].

### Stage 5 — Live inference

| | |
|---|---|
| **Code** | `app/services/live/signal_worker.py` |
| **Reads** | `CH.ohlcv_1m` (recent bars), in-memory cached model |
| **Writes** | `CH.signals`, `CH.agent_runs` |
| **Where** | Standalone process (see [09_scalability.md](09_scalability.md) — decoupled from uvicorn) |
| **Latency budget** | <500ms from bar arrival to signal write |

```python
# app/services/live/signal_worker.py
from app.ml.features.v1 import compute, FEATURE_NAMES   # ← same module as training
from app.ml.registry import load_prod_model

model = load_prod_model("swing_5m_xgb")   # cached singleton

async def on_new_bar(bar: Bar):
    # Pull last 200 bars from CH for window calculations
    history = await ch_queries.recent_bars(bar.symbol, n=200, end=bar.timestamp)

    # Compute features — SAME function as training
    features_df = compute(history)
    latest_features = features_df.iloc[-1][FEATURE_NAMES].values.reshape(1, -1)

    # Score
    score = model.predict(latest_features)[0]

    if abs(score) > THRESHOLD:
        await signals_repo.insert(Signal(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            score=float(score),
            model_name="swing_5m_xgb",
            model_version=model.version,
            feature_set_version="v1",
        ))
```

**The critical line is `from app.ml.features.v1 import compute`.**
The same function ran during training. No re-implementation in SQL
or a separate inference module. This is the #1 ML bug source and
the #1 thing this design prevents.

### Stage 6 — Monitoring

Three monitors, three response modes:

| Metric | Source | Trigger | Response |
|---|---|---|---|
| **Feature drift** | `data.feature_drift_metrics` (daily Spark job comparing live vs training distributions via KS test) | `drift_severity = 'alert'` for 3 consecutive days | Email; investigate; consider retraining |
| **Prediction calibration** | Live `signals` joined with realized forward returns (hourly CH MV) | Predicted-vs-realized correlation drops >20% from training | Page; consider rollback to previous model version |
| **PnL attribution** | Daily aggregation of `sim_trades` vs backtest expectation | Live PnL underperforms backtest expectation by >2σ over 10 days | Freeze model (status → 'retired'); investigate before reinstating |

All three are CH MVs or daily Spark jobs writing to `data.*` tables.
Cockpit `/app/ml/health` page renders them.

### Stage 7 — Retraining loop

Weekly cron (Sunday 08:00 UTC, after feature/label builds):

1. **Refresh features** — incremental: only new bars since last snapshot
2. **Refresh labels** — same
3. **Train challenger** on a rolling 4-year window ending last Friday
4. **Walk-forward backtest** challenger vs prod on a 30-day hold-out
5. **Auto-promote rule:**
   - If challenger hold-out Sharpe > prod hold-out Sharpe + 0.5 → mark challenger as `canary`
   - Canary takes 10% of live inferences for 14 days
   - If canary live-PnL ≥ challenger backtest expectation → promote to `prod`, retire previous prod
   - Otherwise: retire challenger, keep prod

Promotion is a single UPDATE to `data.model_registry.status`. The
signal worker re-reads the registry every 60s.

## Reproducibility — the snapshot pin contract

Every model artifact directory contains a `snapshot_ids.json`:

```json
{
  "model_name": "swing_5m_xgb",
  "model_version": "2025-05-20-a3f1",
  "feature_set_version": "v1",
  "label_version": "v1_fwd5m",
  "snapshots": {
    "lake.data.polygon_adjusted":       4218764512837645000,
    "lake.data.schwab_universe":        7843219874523648000,
    "lake.data.market_corp_actions":    9012345678901234567,
    "lake.data.features_1m_v1":         1234567890123456789,
    "lake.data.labels_v1_fwd5m":        2345678901234567890,
    "lake.data.point_in_time_universe": 3456789012345678901
  },
  "train_start": "2020-01-01",
  "train_end":   "2024-06-30",
  "trained_at":  "2025-05-20T14:23:11Z"
}
```

A year later, anyone can recreate the training set:

```python
features = spark.sql("""
    SELECT * FROM lake.data.features_1m_v1
    VERSION AS OF 1234567890123456789
""")
# ... identical to what was trained on
```

Iceberg's snapshot retention (`90 days` per `03_s3_layout.md`) must
be **lifted** for snapshots referenced by production models. Use
table tags:

```sql
ALTER TABLE lake.data.features_1m_v1
CREATE TAG `model-swing_5m_xgb-2025-05-20-a3f1`
AS OF VERSION 1234567890123456789
RETAIN 5 YEARS;
```

Tagged snapshots survive `expire_snapshots`.

## Train/serve parity — the contract

The single rule that prevents the most common ML bug:

> The function that computes features at training time MUST be the
> exact same Python function that computes features at inference
> time. No SQL reimplementation. No "close enough" Numpy version.

Enforced by:
1. **One module** — `app/ml/features/v{N}.py` — imported by both
   the Spark driver (`scripts/spark/feature_build_v{N}.py`) and the
   live signal worker (`app/services/live/signal_worker.py`).
2. **Contract test** — `tests/test_feature_parity.py` reads 100
   bars from CH, computes features both via the live path and the
   Spark path (on a tiny local Spark), and asserts the two outputs
   are numerically identical to 1e-9.
3. **Versioning is immutable** — once `v1.py` ships, never modify.
   New features → new module `v2.py`, new feature table
   `features_1m_v2`. Old models keep working against the old table.

## Cost model

| Stage | Runtime | Cadence | Cost/run |
|---|---|---|---|
| Feature build (whole market, 5y) | EMR Serverless, ~30 min | Weekly | ~$3 |
| Feature build (incremental, 1 week) | EMR Serverless, ~3 min | Daily | ~$0.30 |
| Label build (whole market, 5y) | EMR Serverless, ~10 min | Weekly | ~$1 |
| Train (XGBoost, 5y × 5k symbols) | SageMaker ml.m5.4xlarge, ~30 min | Weekly + ad-hoc | ~$5 |
| Backtest (1 model, 30-day hold-out) | Local Python, ~5 min | Per challenger | ~$0 |
| Live inference | In-process, signal_worker | Continuous | ~$0 (uses existing live tier compute) |
| Drift monitor | EMR Serverless, ~5 min | Daily | ~$0.50 |
| Storage (features_1m_v1, 5 versions retained) | S3 Standard | n/a | ~$5/month |

**Total ML pipeline operating cost: ~$15-25/month** for one model
on a 5k-symbol universe with weekly retraining.

## What this replaces from v1

| v1 plan | v2 ML pipeline equivalent |
|---|---|
| `gold.features_1m` (designed, not implemented) | `data.features_1m_v{N}` (this doc) |
| `gold.universes` (designed) | `data.point_in_time_universe` (this doc) |
| `feature_set_version` column (designed) | Now also `source_snapshot_id` for full lineage |
| Ad-hoc model training in notebooks | `scripts/ml/train_v{N}.py` with model_registry |
| (no formal monitoring) | drift + calibration + PnL attribution monitors |

## Open decisions

These belong as new gates in [`08_decisions.md`](08_decisions.md):

1. **Training compute** — SageMaker training jobs (managed, ~$5/run)
   vs local Python on a dev box ($0 but no persistence/queueing).
   Recommend: SageMaker for prod retraining; local for experiments.

2. **Universe history source** — Polygon historical tickers endpoint
   (~$0/month at our tier) vs manually-curated CSV (free but stale).
   Recommend: Polygon, refreshed daily.

3. **Model artifact format** — pickle (Python-only, fragile across
   library versions) vs ONNX (portable, slower for tree models) vs
   XGBoost native binary format. Recommend: native format per model
   library (XGBoost binary, LightGBM binary); avoid pickle.

4. **Canary traffic split** — fixed 10% vs gradual ramp (10% → 25% →
   50% → 100%). Recommend: fixed 10% for 14 days; manual promotion.

5. **Drift alert thresholds** — KS-statistic > 0.1 vs > 0.2 vs
   feature-specific. Recommend: 0.15 with 3-consecutive-day
   smoothing to avoid flapping.

## See also

- [01_architecture.md](01_architecture.md) — tier separation; live tier hosts inference, lake hosts training
- [02_schema.md](02_schema.md) — canonical OHLCV schema; features extend it
- [04_spark.md](04_spark.md) — Spark patterns used by feature/label/drift jobs
- [06_migration.md](06_migration.md) — feature tables land after Phase 1 (need adjusted lake first)
- [trading_subsystem_design.md](../trading_subsystem_design.md) — sim/ contract; backtest reads from this pipeline
