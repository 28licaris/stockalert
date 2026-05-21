# 07 — Operator Runbook

Day-to-day procedures for running the v2 system.

## Live tier — common operations

### Restart uvicorn (most common)

```bash
cd /path/to/stockalert
# Clean shutdown
pkill -TERM -f 'uvicorn app.main_api'
sleep 3

# Restart
SILVER_DERIVED_ADD_MEMBERS_ENABLED=false \
  poetry run uvicorn app.main_api:app --reload --port 8000 --host 127.0.0.1
```

Verify:
```bash
curl http://localhost:8000/api/v1/stream/status
# expect: started=true, provider="schwab", streaming_count=N (>0)
```

### Refresh expired Schwab OAuth token

```bash
cd /path/to/stockalert
poetry run python scripts/schwab_get_refresh_token.py
# Browser opens; sign into Schwab; paste returned URL into prompt.
# Script writes new token to data/.schwab_refresh_token AND .env.
# Restart uvicorn (above) so it re-reads the token.
```

Symptoms of expired token (in uvicorn log):
```
ERROR - Schwab streamer: token/principals failed: ... invalid_grant
```

### Add a symbol to the streaming universe

Cockpit UI: `/app/stream` → "+ Add ticker" input.

API:
```bash
curl -X POST http://localhost:8000/api/v1/stream \
  -H "Content-Type: application/json" \
  -d '{"symbol": "PG", "notes": "added by ops"}'
```

Verify the warmup chain fired:
```bash
# Should see in uvicorn log within ~5s of add:
# "Stream warmup tip-fill: PG fetched=N bronze=N ch=N"
# Then 1-min bars start appearing in CH:
curl "http://localhost:8123/?database=stocks" \
  -H "X-ClickHouse-User: default" -H "X-ClickHouse-Key: $CH_PW" \
  --data-binary "SELECT count() FROM ohlcv_1m WHERE symbol='PG'"
```

### Remove a symbol from streaming

```bash
curl -X DELETE http://localhost:8000/api/v1/stream/PG
```

Or cockpit `/app/stream` → click ✕ on the row.

Universe is sticky — removing from a watchlist does NOT remove from
streaming. Only DELETE `/api/v1/stream/{sym}` does that.

### Trigger a scheduled job manually

Cockpit `/app/status` → Scheduled jobs table → click ▶ on any job.

API:
```bash
curl -X POST http://localhost:8000/api/v1/jobs/backfill_gap_sweeper/run
```

Responses:
- `{"status":"started", ...}` → job is running in the background; check
  `/api/v1/jobs` for `last_success` to update
- `{"status":"already_running", ...}` → wait for the current run to finish
- `{"status":"not_found"}` → name typo

### Check what's actively streaming

```bash
curl http://localhost:8000/api/v1/stream/status | jq '.streaming_count, .streaming_symbols'
```

### Check current CH bar flow

```bash
# Bars received in the last 5 minutes
curl "http://localhost:8123/?database=stocks" \
  -H "X-ClickHouse-User: default" -H "X-ClickHouse-Key: $CH_PW" \
  --data-binary "
    SELECT source, count() AS bars, uniqExact(symbol) AS uniq
    FROM ohlcv_1m
    WHERE timestamp > now() - INTERVAL 5 MINUTE
    GROUP BY source ORDER BY bars DESC
    FORMAT PrettyCompactMonoBlock
  "
```

Expected output during market hours: ~100+ symbols receiving bars
from `source = "schwab-live"`.

## Lake tier — Polygon history pulls

### Initial bulk-load (Phase 1A — CV4 one-time op, via Athena)

After CV1-CV5 + CV10' ship and `equities.polygon_raw` exists but is
empty, populate it from the **existing 5y of Polygon flat-file
Parquets already in our S3** at
`s3://stockalert-lake/raw/provider=polygon-flatfiles/kind=minute/`.

We do NOT re-query Polygon — the data is already in our bucket from
the original v1 ingest, paid for once. The Athena server-side import
reads + writes entirely inside AWS:

```bash
cd /path/to/stockalert
export AWS_PROFILE=stockalert-prod
poetry run python scripts/lake_import_athena.py
```

Wall-clock: ~5 minutes (whole-market 5y, ~2.1B rows). Cost: ~$0.20 in
Athena scan fees. The script drops + recreates `equities.polygon_raw`
then runs a single Athena `INSERT INTO … SELECT … WHERE symbol IS NOT
NULL AND timestamp IS NOT NULL` from the partition-projected external
table, followed by `OPTIMIZE … BIN_PACK` for sort-clustered output.

Preflight guard: if `equities.polygon_raw` already has rows (e.g. the
CV7 nightly cron is live), the script refuses without `--force`. Run
THIS step BEFORE deploying CV7 to production. Operational sequencing
in the cutover window:

1. Land CV1-CV5 + CV10' (this script's code). Production unaffected.
2. Run `lake_import_athena.py` — populates `equities.polygon_raw`.
3. Run corp-actions full backfill (`scripts/run_corp_actions_backfill.py --full`).
4. Run `polygon_adjustment_job` whole-market (recipe below) —
   populates `equities.polygon_adjusted`.
5. Deploy CV7-CV9 + restart uvicorn. From the next nightly run,
   diffs land in equities.*.

If you need to pull MORE history later (e.g. Polygon plan upgrade
covering 2016-2020), see "Extending history" below — that path DOES
query Polygon because the new years aren't in our S3 cache yet.

Verify post-run:
```bash
poetry run python -c "
from app.services.iceberg_catalog import get_catalog
from app.services.equities.tables import ensure_polygon_raw
t = ensure_polygon_raw(get_catalog())
print('rows:', t.scan().to_arrow().num_rows)
"
```

### Extending history (future Polygon subscription upgrade)

When the Polygon plan covers more years than `equities.polygon_raw`
currently holds (e.g. 5y → 10y upgrade), pull the new window:

```bash
# Example: pull 2016-2020 after upgrading to 10y coverage
poetry run python scripts/polygon_history_backfill.py \
  --since 2016-01-04 \
  --until 2020-12-31 \
  --concurrency 4
```

Idempotent — the script pre-scans for already-loaded dates and only
fetches the missing ones, so it's safe to broaden a window and re-run
the same command. After the load:

```bash
# Recompute adj_factor for the new history range
poetry run python scripts/spark/polygon_adjustment_job.py \
  --symbols ALL --since 2016-01-04 --until 2020-12-31
```

### Recover a known-bad partition

When a corp-action correction is published or a Polygon flat-file is
republished after an error, the existing rows for the affected window
are wrong. Recipe:

```bash
# 1. Drop the affected partitions via Spark SQL or DuckDB
poetry run python -c "
from app.services.iceberg_catalog import get_catalog
from app.services.equities.tables import ensure_polygon_raw
t = ensure_polygon_raw(get_catalog())
t.delete(\"timestamp >= '2024-05-13' AND timestamp < '2024-05-15'\")
"

# 2. Re-pull with --force (bypass the pre-scan since the table
#    technically has rows in this window — they're stale)
poetry run python scripts/polygon_history_backfill.py \
  --since 2024-05-13 --until 2024-05-14 --force

# 3. Recompute adj_factor for the affected symbols
poetry run python scripts/spark/polygon_adjustment_job.py \
  --symbols AFFECTED1,AFFECTED2 --since 2024-05-13
```

## Lake tier — Spark batch jobs

### Run `polygon_adjustment_job` for one symbol (dev)

```bash
export STOCKALERT_SPARK_LOCAL_MODE=true
export STOCK_LAKE_BUCKET_S3=s3://stockalert-lake/iceberg/
export AWS_PROFILE=stockalert-dev

cd /path/to/stockalert
poetry run python scripts/spark/polygon_adjustment_job.py \
  --symbols AAPL --since 2024-01-01
```

Expected: ~30s for one symbol, ~12 months. Output in
`s3://stockalert-lake/iceberg/equities/polygon_adjusted/`.

### Run `polygon_adjustment_job` whole-market (production)

```bash
# Submit to EMR Serverless
aws emr-serverless start-job-run \
  --application-id "$EMR_APP_ID" \
  --execution-role-arn arn:aws:iam::ACCT:role/stockalert-spark-emr \
  --name "polygon_adjust_full_$(date -u +%Y%m%d)" \
  --job-driver '{
    "sparkSubmit": {
      "entryPoint": "s3://stockalert-code/spark/polygon_adjustment_job.py",
      "entryPointArguments": [],
      "sparkSubmitParameters": "--conf spark.executor.cores=4 --conf spark.dynamicAllocation.enabled=true --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.6.0,org.apache.iceberg:iceberg-aws-bundle:1.6.0"
    }
  }'

# Get the job-run id from output, then watch:
aws emr-serverless get-job-run \
  --application-id "$EMR_APP_ID" \
  --job-run-id "$JOB_RUN_ID"
```

Wall-clock: ~1-2 hours whole-market 5y. Cost: ~$2-3.

### Run incremental corp-action update

After new corp_actions land (Polygon weekly cron):

```bash
# Find affected symbols
SYMBOLS_DIRTY=$(poetry run python -c "
from app.services.silver.ohlcv.build import find_corp_action_dirty_symbols
print(','.join(find_corp_action_dirty_symbols(since='2025-05-13')))
")

# Re-adjust just those
aws emr-serverless start-job-run \
  --application-id "$EMR_APP_ID" \
  --execution-role-arn ... \
  --job-driver '{
    "sparkSubmit": {
      "entryPoint": "s3://stockalert-code/spark/polygon_adjustment_job.py",
      "entryPointArguments": ["--symbols", "'$SYMBOLS_DIRTY'", "--since", "2020-01-01"]
    }
  }'
```

Per-symbol incremental run: ~1 min each on EMR Serverless.

### Run lake compaction (weekly maintenance)

```bash
# Schedule weekly via EventBridge → Lambda → emr-serverless start-job-run
# scripts/spark/compact_lake.py invokes:

spark.sql("""
  CALL lake.system.rewrite_data_files(
    table => 'lake.equities.schwab_universe',
    options => map(
      'target-file-size-bytes', '134217728',
      'min-file-size-bytes', '67108864'
    )
  )
""")
```

Repeat for `equities.polygon_adjusted` (less frequent — monthly is fine).

### Expire old Iceberg snapshots

```bash
# Drops snapshots older than 90 days (retained snapshots are still
# accessible via VERSION AS OF). Reduces metadata bloat.
spark.sql("""
  CALL lake.system.expire_snapshots(
    table => 'lake.equities.polygon_adjusted',
    older_than => TIMESTAMP '$(date -u -d '90 days ago' +%Y-%m-%dT%H:%M:%S)Z',
    retain_last => 20
  )
""")
```

Run weekly. Combined cost (compaction + expire) ~$1/month on EMR Serverless.

## Disaster recovery procedures

### Restore CH `ohlcv_1m` from `equities.schwab_universe`

If ClickHouse is wiped or corrupted:

```python
# scripts/restore_ch_from_lake.py
import duckdb
from app.db.client import get_client
from app.db.universe_repo import list_active_stream_universe

ch = get_client()

# stream_universe is a CH-only table (canonical "what we hot-cache").
# After CH restore, this row set is what we want filled in ohlcv_1m. If CH
# is totally wiped (universe gone too), re-create stream_universe from the
# operator-curated CSV at data/stream_universe_seed.csv before running this.
syms = [r["symbol"] for r in list_active_stream_universe(ch)]
syms_csv = ",".join(f"'{s}'" for s in syms)

df = duckdb.sql(f"""
    SELECT symbol, timestamp, open, high, low, close, volume, vwap, trade_count, source
    FROM iceberg_scan('s3://stockalert-lake/iceberg/equities/schwab_universe/')
    WHERE symbol IN ({syms_csv})
""").arrow()

# Bulk insert
ch.insert_arrow("ohlcv_1m", df)
print(f"restored {df.num_rows:,} rows across {len(syms)} symbols")
```

Wall-clock: ~5-15 min for the universe.

### Recover from a failed adjustment job

If `polygon_adjustment_job` writes corrupted data:

```python
# Find the last known-good snapshot
spark.sql("SELECT * FROM lake.equities.polygon_adjusted.snapshots ORDER BY committed_at DESC LIMIT 10").show()

# Roll back to a specific snapshot
spark.sql("""
    CALL lake.system.rollback_to_snapshot(
      'lake.equities.polygon_adjusted',
      <snapshot_id>
    )
""")
```

This is a soft rollback — the bad snapshot is still in metadata
until `expire_snapshots` cleans it up. You can verify rollback by:
```python
spark.sql("SELECT count(*) FROM lake.equities.polygon_adjusted").show()
```

## Monitoring

### Daily checks

| Check | Command / URL | Healthy? |
|---|---|---|
| Live API up | `curl localhost:8000/health` | `{"status":"ok"}` |
| Schwab streaming | `curl localhost:8000/api/v1/stream/status` | `provider_ready=true, streaming_count > 0` |
| Bars flowing | CH query for last 5min `ohlcv_1m` | `> 50 symbols × > 100 bars` during market hours |
| Job statuses | `curl localhost:8000/api/v1/jobs` | No `last_status="error"` |
| Disk usage on CH | `du -sh /var/lib/clickhouse/` | < 50 GB |

### Weekly checks

| Check | Command | Healthy? |
|---|---|---|
| Iceberg file count | `aws s3 ls s3://stockalert-lake/iceberg/equities/polygon_adjusted/data/ --recursive \| wc -l` | < 10,000 (else schedule compaction) |
| Iceberg metadata size | `aws s3 ls s3://stockalert-lake/iceberg/equities/polygon_adjusted/metadata/ --recursive --summarize` | < 1 GB |
| EMR Serverless cost MTD | CloudWatch metric | < $20 |
| S3 storage MTD | S3 Storage Lens | < 1 TB |

### Alerts to wire up

- **Schwab WS disconnected for >5 min**: cockpit Status page already shows; add SNS alert.
- **No bars in `ohlcv_1m` for >2 min during market hours**: add CH-side alert.
- **EMR cost spike >$50/day**: CloudWatch alarm.
- **Glue catalog API errors**: CloudWatch alarm.

## Cost monitoring

### S3 storage
```bash
aws s3 ls s3://stockalert-lake/ --recursive --summarize | tail -2
```
Expected: ~150 GB total. Growth: ~1 GB/month.

### EMR Serverless usage
```bash
aws emr-serverless list-job-runs \
  --application-id "$EMR_APP_ID" \
  --max-results 50 \
  --query 'jobRuns[].{Name:name,Cost:totalResourceUtilization}' \
  --output table
```

Expected: ~10-15 runs/month total. Monthly cost: $5-15.

### Polygon subscription
Fixed monthly. If not actively running `nightly_polygon_refresh` and
not running `polygon_corp_actions_ingest`, consider downgrading or
canceling.

## Common operator scripts

| Script | What it does | When to run |
|---|---|---|
| `scripts/schwab_get_refresh_token.py` | OAuth flow to get new Schwab refresh token | Token expired or first setup |
| `scripts/spark/polygon_adjustment_job.py` | Build `equities.polygon_adjusted` | Weekly cron + on-demand |
| `scripts/spark/lake_archive_job.py` | Periodic CH → S3 Iceberg flush | Hourly cron |
| `scripts/spark/compact_lake.py` | Iceberg file compaction | Weekly cron |
| `scripts/restore_ch_from_lake.py` | Restore CH from S3 backup | Disaster recovery |
| `scripts/migrations/copy_bronze_to_data_polygon_raw.py` | One-time v1 → v2 migration | Phase 1 of v2 migration |

## See also

- [01_architecture.md](01_architecture.md) — what the system looks like
- [04_spark.md](04_spark.md) — Spark job details
- [05_providers.md](05_providers.md) — provider-specific procedures (Schwab token, etc.)
- [06_migration.md](06_migration.md) — migration phases
