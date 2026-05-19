# Runbook — Silver --full → ClickHouse hot-reload

End-to-end operator procedure for refreshing the silver lake from
bronze + hot-loading ClickHouse from the refreshed silver. The
"wipe-and-rebuild" pattern documented here is **expected** under our
architecture (silver = canonical, CH = derived cache, re-buildable
any time); not a disaster recovery.

**When to run this:**

- After a silver schema change (new column, changed adjustment logic).
- After a bronze backfill that materially changes the data window
  (e.g. extending Polygon coverage from 5y → 20y per
  `runbook_extend_polygon_history.md`).
- After investigating a data-correctness issue and fixing the inputs
  (corp_actions gap, provider revision, etc.).
- When CH drifts from silver and you want a clean re-base.

**When NOT to run this:**

- Routine nightly bronze refresh — the nightly silver build +
  `add_members` flow handle incremental updates.
- Single-symbol fixes — use `silver_to_ch_backfill` for one symbol.

**Wall-clock budget:** ~1 hour total
- Pre-flight: ~30 sec
- Silver corp_actions rebuild: ~1 min local
- Silver --full via CodeBuild (seed): ~36 min cloud
- Spot-checks: ~30 sec
- CH wipe + reload: ~10-15 min

**Cost:** ~$0.50 (CodeBuild only).

---

## Step 0 — Pre-flight

Verify nothing is mid-flight that the procedure will conflict with:

```bash
# 1. No active CodeBuild silver run
aws --profile stock-lake codebuild list-builds-for-project \
    --region us-east-1 \
    --project-name sockalert-silver-full-backfill \
    --query 'ids[0]' --output text \
  | xargs -I {} aws --profile stock-lake codebuild batch-get-builds \
    --region us-east-1 --ids {} \
    --query 'builds[0].buildStatus' --output text
# Expected: SUCCEEDED or FAILED (not IN_PROGRESS)

# 2. FastAPI hasn't auto-started a build (check the journal log)
ps aux | grep run_silver_ohlcv_build | grep -v grep
# Expected: nothing
```

Run the silver-build preflight script (verifies catalog reachability,
bronze coverage, corp_actions year-coverage, ability to create silver
tables, end-to-end slice):

```bash
poetry run python scripts/preflight_silver_build.py
```

**Gate:** all 7 checks 🟢 OK (warnings 🟡 are acceptable; FAIL 🔴 is blocking).

---

## Step 1 — Rebuild `silver.corp_actions` from bronze

Drops the existing silver corp_actions table and rebuilds via a single
fast append (TA-5.1.12-style append-on-empty optimization).

```bash
# Drop the table
AWS_PROFILE=stock-lake AWS_REGION=us-east-1 poetry run python -c "
from pyiceberg.catalog import load_catalog
from app.services.iceberg_catalog import _build_catalog_properties
from app.services.silver.schemas import silver_table_id
cat = load_catalog('fresh', **_build_catalog_properties())
cat.drop_table(silver_table_id('corp_actions'))
print('silver.corp_actions dropped')
"

# Rebuild from existing bronze.{provider}_corp_actions
AWS_PROFILE=stock-lake AWS_REGION=us-east-1 PYTHONUNBUFFERED=1 \
poetry run python scripts/run_corp_actions_backfill.py \
    --silver-only --since 2003-01-01
```

**Expected:** `~1.5M rows`, `~40 sec`, `write_strategy=append`.

**If `write_strategy=upsert` shows up instead:** the target table wasn't
empty. Drop it explicitly and re-run.

**Verify critical splits landed:**

```bash
AWS_PROFILE=stock-lake AWS_REGION=us-east-1 poetry run python -c "
from pyiceberg.catalog import load_catalog
from app.services.iceberg_catalog import _build_catalog_properties
from app.services.silver.schemas import silver_table_id
import pyarrow as pa
import pyarrow.compute as pc
cat = load_catalog('fresh', **_build_catalog_properties())
t = cat.load_table(silver_table_id('corp_actions'))
arrow = t.scan().to_arrow()
key = ['AAPL','NVDA','TSLA','AMZN','GOOGL','MSFT','NFLX']
mask = pc.and_(pc.equal(arrow['action_type'], 'split'),
               pc.is_in(arrow['symbol'], pa.array(key)))
splits = arrow.filter(mask).select(['symbol','ex_date','factor']).sort_by([('ex_date','descending')])
print(f'Found {splits.num_rows} key splits')
for r in splits.to_pylist():
    print(f\"  {r['symbol']:6} {str(r['ex_date']):<12} factor={r['factor']}\")
"
```

**Gate:** at least 16 splits across the seed-universe symbols
(AAPL 2020, NVDA 2024+2021, TSLA 2020+2022, AMZN 2022, GOOG/GOOGL 2022,
NFLX 2025+2015, AAPL 2014, older NVDA/AAPL/MSFT/NFLX rows).

---

## Step 2 — Drop `silver.ohlcv_1m` + `silver.bar_quality`

```bash
AWS_PROFILE=stock-lake AWS_REGION=us-east-1 poetry run python -c "
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError
from app.services.iceberg_catalog import _build_catalog_properties
from app.services.silver.schemas import silver_table_id
cat = load_catalog('fresh', **_build_catalog_properties())
for short in ('ohlcv_1m', 'bar_quality'):
    try:
        cat.drop_table(silver_table_id(short))
        print(f'silver.{short}: dropped')
    except NoSuchTableError:
        print(f'silver.{short}: already absent')
"
```

**Why drop?** Empty target triggers the TA-5.1.12 append-on-empty fast
path in the silver build. Without it, every commit pays merge cost.

---

## Step 3 — Trigger CodeBuild silver --full (seed)

**One-liner that triggers + watches to completion** (recommended):

```bash
scripts/trigger_silver_codebuild.sh --symbols seed --watch
```

That wraps the AWS CLI calls, polls every 60s, pulls the result.json
from S3, prints a summary, and exits 0 only on SUCCEEDED + zero
slice failures.

**Manual flow** (if you want to detach and check back later):

```bash
# Step 3a — trigger and capture the build ID
BUILD_ID=$(scripts/trigger_silver_codebuild.sh --symbols seed | grep build_id | awk '{print $NF}')

# Step 3b — later, watch to completion (poll + report)
scripts/watch_silver_codebuild.sh "$BUILD_ID"
```

**Expected:** ~36 min, status=SUCCEEDED, `write_strategy=append`
in CloudWatch logs, `slices_failed: 0` in the report.

**Gate:**
- `status: ok`
- `slices_failed: 0`
- `silver_rows: ~60-70M` (for seed scope; whole-market would be ~5-6B)

---

## Step 4 — Spot-check split-adjustment math

```bash
poetry run python scripts/spot_check_silver_adjustments.py
```

This reads silver bars for 8 known (symbol, date) tuples spanning
well-known splits (AAPL 4:1, NVDA 10:1, TSLA 3:1, AMZN 20:1, GOOGL
20:1, etc.) and asserts the close prices are in the adjusted range
within ±5%. **No external Yahoo HTTP dependency — expected values are
inlined.**

**Gate:** all 8 checks PASS. Any FAIL = silver math is wrong, do NOT
proceed to CH load. Common causes:
- Missing split in `silver.corp_actions` for that symbol
- Silver build skipped the factor multiplication
- Wrong (symbol, date) coverage in silver

---

## Step 5 — Wipe + reload ClickHouse

**This is destructive** — `TRUNCATE TABLE ohlcv_1m` empties CH.
**Reversible** because silver is the source of truth, but make sure
no live alerts depend on the current CH state mid-procedure (the
dashboard will show empty data for ~10 min during reload).

```bash
poetry run python scripts/rebuild_ch_from_silver.py \
    --symbols seed --wipe \
    --out-json /tmp/ch_rebuild.json
```

**Expected:** ~10 min wall-clock, `bars_written` matches `bars_read`,
`status: ok`.

**Gate:**
- `status: ok` (or `ok_with_warnings` if the row-delta sanity check
  is < 90% — investigate but not blocking)
- `failed_symbols: 0`

---

## Step 6 — Smoke test

Query CH for the same spot-check symbols and confirm prices match
silver. Use any chart endpoint:

```bash
# Hit the dashboard endpoint (assumes FastAPI is up)
curl -sS "http://localhost:8000/api/silver/bars/AAPL?start=2020-08-28T19:00:00Z&end=2020-08-28T21:00:00Z" \
  | jq '.bars[-1].close'
# Expected: ~124.81 (the 4:1 split's adjusted close)

# Cross-check the CH-backed indicator endpoint
curl -sS "http://localhost:8000/api/ohlcv/AAPL?days=5" \
  | jq '.bars[-1].close'
# Should be a current AAPL close (live or last trading day)
```

**Gate:** spot-check values match silver values.

---

## Step 7 — Flip the silver-derived flag (TA-5.1.7 Part C)

Once spot-checks + CH smoke test pass, flip the production flag:

```bash
# .env
SILVER_DERIVED_ADD_MEMBERS_ENABLED=true
```

Restart FastAPI:

```bash
docker compose restart api  # or however you restart in your setup
```

Test the add-symbol flow with a throwaway watchlist symbol that
ISN'T in the seed universe:

```bash
# Pick a non-seed symbol — example: a random Russell 2000 name
curl -X POST "http://localhost:8000/api/watchlists/scratch/members" \
    -H "Content-Type: application/json" \
    -d '{"symbols": ["ZYXI"]}'

# Watch logs for the silver-derived flow:
#   1. silver_to_ch_backfill (will find 0 rows since ZYXI not in silver)
#   2. schwab_rest_tip_fill (fills last 48 days from Schwab REST)
#   3. Schwab streaming subscribes ZYXI
```

For non-seed symbols this will currently load only 48 days of history.
The full whole-market silver materialization is a planned next phase
(see BUILD_JOURNAL TA-5.6 + TA-5.7).

---

## Rollback

If anything goes wrong:

- **Silver build failed:** silver tables are still in their pre-build
  state (Iceberg snapshot isolation). Drop + re-run from Step 1.
- **CH reload failed:** silver is intact. Re-run Step 5 (it's idempotent
  via ReplacingMergeTree).
- **Spot-checks failed:** silver math is broken. Investigate which
  split is missing from `silver.corp_actions`; do NOT load CH.
- **Add-symbol flow broken after flag flip:** flip
  `SILVER_DERIVED_ADD_MEMBERS_ENABLED=false`, restart FastAPI.
  Legacy provider-REST-direct path resumes immediately.

## Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `write_strategy=upsert` in silver build log | target table not empty | Drop + re-run |
| Spot-check shows raw prices (e.g. AAPL=$499) | silver.corp_actions missing the split | Re-run Step 1; verify with the key-splits grep |
| CH row delta < 90% of bars_written | silent insert failure OR ReplacingMergeTree merges in-flight | Re-run after a few minutes; check CH server logs |
| CodeBuild stuck IN_PROGRESS > 60 min | likely OOM-killed; CodeBuild compute too small | Bump CodeBuild project to 7 GB compute |
| `BUILD NO-OP detected` from silver build | python killed mid-write | Investigate CloudWatch logs; the new verify-mutation guard catches this loudly |
