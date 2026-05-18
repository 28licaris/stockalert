# Silver service

Iceberg tables holding **canonical, deduplicated,
corp-action-adjusted OHLCV** plus the corp-actions reference and
bar-quality audit ledger. Layer 2 of the medallion architecture
(bronze → silver → gold).

**Implementation contract:**
[docs/silver_layer_plan.md](../../../docs/silver_layer_plan.md).
Read it before changing anything here.

**Strategic context:**
- Silver is the **canonical store** ([silver_layer_plan §2.1](../../../docs/silver_layer_plan.md)).
  Every OHLCV consumer in the platform reads from silver — chart,
  screener, indicator computation, backtest harness, MCP tools.
  Bronze is read **only** by the silver build job.
- ClickHouse is a **derived hot cache** rebuildable from silver
  byte-identically.
- Gold (when built) is for ML training only.

## What it owns

| File | Owns |
|---|---|
| [schemas.py](schemas.py) | Pydantic `CorpAction` + Iceberg schemas for `silver.corp_actions`, `silver.ohlcv_1m`, `silver.bar_quality` |
| [tables.py](tables.py) | Idempotent Iceberg table creation against the Glue catalog (silver-side tables) |
| [corp_actions/polygon_ingest.py](corp_actions/) (TA-5.0 step 5b) | Polygon REST → `bronze.polygon_corp_actions`. Pulls splits + dividends; idempotent upsert by `(symbol, ex_date, action_type)` |
| [corp_actions/build.py](corp_actions/) (TA-5.0 step 5c) | The bronze → silver merge job: reads every `bronze.{provider}_corp_actions` table, applies provider precedence, writes `silver.corp_actions` |
| (planned TA-5.1) `ohlcv/build.py` | The nightly OHLCV build: merges `bronze.{provider}_minute` tables, applies adjustment factors from `silver.corp_actions`, writes `silver.ohlcv_1m` + `silver.bar_quality` |

## Architectural rule (per [silver_layer_plan §4](../../../docs/silver_layer_plan.md))

**Every silver table is derived from bronze, never written directly
from a provider.** Reasoning: the medallion contract (bronze raw,
silver canonical, gold ML) plus the pluggable-provider principle
(`docs/silver_layer_plan.md §2.3`). When we add a second
corp-actions provider later, it gets a new
`bronze.{provider}_corp_actions` table and an entry in the
precedence config — `silver.corp_actions` keeps the same shape,
zero downstream changes.

## What it does NOT own

- Reads back from silver — that's
  [`app/services/readers/silver_reader.py`](../readers/) (TA-5.2).
- Bronze writes for OHLCV — see [`app/services/bronze/`](../bronze/).
- Bronze writes for corp-actions also go through this package
  (`silver/corp_actions/polygon_ingest.py`) because they're the
  same logical work unit; only OHLCV ingest lives in
  `app/services/ingest/` (existing convention).
- Backfilling silver into ClickHouse — see (planned TA-5.3)
  `app/services/ingest/silver_to_ch_backfill.py`.

## Tables

Current (or pending):

| Table | Status | Purpose |
|---|---|---|
| `stock_lake.corp_actions` | TA-5.0 in flight | Splits + cash dividends + stock dividends + spinoffs, keyed by `(symbol, ex_date, action_type)` |
| `stock_lake.ohlcv_1m` | TA-5.1 planned | Canonical 1-min bars, both raw + corp-action-adjusted columns |
| `stock_lake.bar_quality` | TA-5.1 planned | Per-`(symbol, date)` data-quality ledger (expected vs actual bars, provider disagreements, etc.) |

Naming note (same convention as bronze): Glue databases are flat;
silver/bronze/gold are separated only by table-name prefix +
on-disk S3 prefix (`s3://${bucket}/iceberg/silver/<table>/`). The
fully-qualified catalog identifier is `stock_lake.corp_actions`
(no `silver.` namespace).

## Schema (`corp_actions`)

| Field | Type | Required | Notes |
|---|---|---|---|
| symbol | string | yes | identifier |
| ex_date | date | yes | identifier — ex-dividend / ex-split date |
| action_type | string | yes | identifier — `split` / `cash_dividend` / `stock_dividend` / `spinoff` |
| factor | double | no | split ratio (4.0 for 4-for-1); stock-div ratio; null for cash divs |
| cash_amount | double | no | dividend per share (USD); null for splits |
| announced_at | timestamptz | no | when the action was announced |
| source_provider | string | yes | `polygon` (the canonical source) |
| ingestion_ts | timestamptz | no | when this silver row was written |
| ingestion_run_id | string | no | FK to CH `ingestion_runs` |

Identifier `(symbol, ex_date, action_type)` — used by Iceberg
`MERGE INTO` for idempotent re-ingestion when Polygon revises a
prior announcement.

## Usage (TA-5.0)

```python
from app.services.silver import CorpAction
from app.services.silver.tables import ensure_silver_corp_actions
from app.services.silver.corp_actions_ingest import CorpActionsIngest

# One-shot historical backfill (operator-triggered).
ingest = CorpActionsIngest.from_settings()
await ingest.backfill_full_history(since=date(2003, 1, 1))

# Nightly incremental.
await ingest.run_nightly()
```

## Tests

```bash
poetry run pytest tests/test_silver_corp_actions.py -v
```
