# Runbook — extending Polygon coverage further back

You're on a 5-year Polygon plan and want to upgrade to a longer
window (e.g. 20-year). This runbook covers the four-step sequence:
upgrade plan → bronze backfill → corp_actions backfill → silver
rebuild.

**Estimated wall-clock (5y → 20y):**
- Bronze flat-files backfill: ~12-24 hours (via CodeBuild)
- Corp_actions backfill: ~45-60 min
- Silver `--full` rebuild: ~2-3 hours (via CodeBuild)
- Verification: ~10 min

**Total: ~15-30 hours, mostly unattended.**

---

## Step 1 — Upgrade Polygon subscription

Sign in to polygon.io → upgrade to the higher-tier Stocks plan with
the deeper history you want (e.g. "Currencies & Stocks 20 years").
Verify by checking the date range in their Console for any historical
endpoint.

## Step 2 — Set the new start date in config

Edit `.env`:

```bash
# Change this from "2021-01-04" to whatever new floor you want:
BRONZE_HISTORY_START=2006-01-04
```

This is read by:
- `silver --full` default `since` argument
- `silver --rebuild-corp-action-dirty` lower bound for rebuilds
- The default `since` in any future silver-rebuild script

Restart FastAPI (or any long-running process) to pick up the new value.

## Step 3 — Bronze flat-files backfill (the heavy lift)

```bash
# Trigger via CodeBuild — same project as silver --full uses,
# but a different buildspec for the polygon backfill side.
# OR run locally if you have time + reliable network:

AWS_PROFILE=stock-lake AWS_REGION=us-east-1 \
  poetry run python scripts/polygon_flatfiles_bulk_backfill.py \
    --start 2006-01-04 \
    --end $(date -u +%Y-%m-%d)
```

Wall-clock: ~12-24 hours from a residential laptop; ~4-6 hours via
CodeBuild same-region.

Bronze grows from ~30 GB → ~120 GB. Cost stays minimal (S3 Standard,
~$3/month at 120 GB).

When done, verify:
```bash
AWS_PROFILE=stock-lake AWS_REGION=us-east-1 poetry run python -c "
from app.services.iceberg_catalog import get_catalog
from app.services.bronze.schemas import bronze_table_id
cat = get_catalog()
t = cat.load_table(bronze_table_id('polygon_minute'))
m = t.current_snapshot().summary.additional_properties
print('bronze.polygon_minute rows:', m.get('total-records'))
print('expect: ~8-10B for 20-year whole-market')
"
```

## Step 4 — Corp_actions backfill for the extended window

```bash
AWS_PROFILE=stock-lake AWS_REGION=us-east-1 \
  poetry run python scripts/run_corp_actions_backfill.py \
    --since 2006-01-04 \
    --until $(date -u +%Y-%m-%d) \
    --out-json /tmp/corp_actions_20y.json
```

With the year-chunking fix landed in `polygon_ingest.py`, this runs
~30-45 min for 20 years (vs. silent OOM on the unchanked version).

**Important:** ALSO drop + rebuild silver.corp_actions if you already
ran silver_corp_actions_build with a narrower window before. Otherwise
the older silver rows survive and the OHLCV build references the
wrong (truncated) split set.

```bash
# Drop silver.corp_actions:
AWS_PROFILE=stock-lake AWS_REGION=us-east-1 poetry run python -c "
from app.services.iceberg_catalog import get_catalog
from app.services.silver.schemas import silver_table_id
get_catalog().drop_table(silver_table_id('corp_actions'))
"

# Re-merge bronze → silver corp_actions (writes via fast append since target is empty):
poetry run python scripts/run_corp_actions_backfill.py \
    --silver-only \
    --since 2006-01-04 \
    --until $(date -u +%Y-%m-%d)
```

## Step 5 — Drop silver OHLCV + rebuild via CodeBuild

```bash
# Drop silver OHLCV tables — gets us the fast append-on-fresh path:
AWS_PROFILE=stock-lake AWS_REGION=us-east-1 poetry run python -c "
from app.services.iceberg_catalog import get_catalog
from app.services.silver.schemas import silver_table_id
cat = get_catalog()
for short in ('ohlcv_1m', 'bar_quality'):
    cat.drop_table(silver_table_id(short))
"
```

Then in the CodeBuild project's **Environment variables**, set:

```
SILVER_BUILD_SINCE = 2006-01-04
SILVER_BUILD_UNTIL = (leave empty for "yesterday")
SILVER_BUILD_MODE  = ""           ← empty (use --since/--until, not --full)
```

Click **Start build**. Expected wall-clock: ~2-3 hours for 20-year ×
100-symbol coverage (~3-4× the time of the 5-year `--full` we ran).

Watch CloudWatch Logs for `write_strategy=append` (confirms fast path).

## Step 6 — Verify

After silver build completes:

```bash
# Pull the report
aws --profile stock-lake s3 cp \
  s3://stock-lake-562741918372-us-east-1-an/silver_build_reports/codebuild-<id>.json \
  /tmp/silver_20y.json

# Spot-check Yahoo on splits in the older window (AAPL 2020, NVDA 2021):
curl -sS "http://localhost:8000/api/silver/bars/AAPL?start=2020-08-28T18:30:00Z&end=2020-08-28T18:31:00Z" | jq '.bars[0].close'
# Expected ~124.81 (= raw ~499 / 4)

# AAPL 2014 7-for-1 split:
curl -sS "http://localhost:8000/api/silver/bars/AAPL?start=2014-06-06T18:30:00Z&end=2014-06-06T18:31:00Z" | jq '.bars[0].close'
# Expected ~92.5 (= raw ~645 / 7)
```

## Step 7 — Cleanup

After validation passes, nothing else to do. Nightly silver build
will pick up new bars automatically. ClickHouse can be rebuilt from
silver any time via the (to-be-built) `scripts/rebuild_ch_from_silver.py`
once TA-5.5 lands.

## Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Silver build OOM during upsert | tables non-empty + huge merge | Drop silver tables before re-running for clean append path |
| Bronze backfill exit silently | Polygon rate limit (free tier) | Confirm subscription active; check Polygon dashboard for quota |
| `write_strategy=upsert` in log instead of `append` | Tables not dropped | Drop them; re-run |
| AAPL 2020 split shows raw prices | Corp_actions window too narrow | Re-run corp_actions backfill with `--since 2006-01-04` (or whatever your new floor is) |

## Why this is reversible

Bronze is append-only and immutable. If you upgrade Polygon then
downgrade, your existing bronze data stays — you just can't extend
further. Same with silver: it's derived; nuke + rebuild any time
from bronze + silver.corp_actions.

The only "permanent" cost of upgrading: S3 storage (~$1-3/month per
100 GB of bronze).
