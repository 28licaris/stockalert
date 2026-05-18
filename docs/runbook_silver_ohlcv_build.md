# Runbook — silver_ohlcv_build (TA-5.1.7)

Operator procedure for first-time silver-OHLCV initial backfill +
ongoing nightly validation. Companion to the design contract in
[silver_layer_plan.md §3](silver_layer_plan.md). The build code,
schedule loop, and operator CLI are all built (TA-5.1.1–.6); this
runbook covers the **operator** steps to turn it on safely.

## The 5-step procedure

### Step 1 — Preflight (~30 seconds)

Validate every wire in the pipeline before kicking off a multi-hour
backfill:

```bash
poetry run python scripts/preflight_silver_build.py
```

The script runs seven checks in dependency order:

1. Iceberg catalog reachable
2. `bronze.polygon_minute` + `bronze.schwab_minute` have rows
3. `silver.corp_actions` is present (or WARN — F=1 without it)
4. `silver.ohlcv_1m` + `silver.bar_quality` can be ensured
5. End-to-end build slice (default `AAPL × yesterday`)
6. Silver readback confirms the row landed
7. CH `ingestion_runs` audit row was recorded

**Pass criterion:** `✅ ALL OK — safe to run --full`. Exit 0.

**Fail criterion:** any `🔴 FAIL`. Exit 2. Do NOT proceed until
each FAIL is addressed (script's message names the fix).

Optional flags:
- `--symbol NVDA` — sanity-check a different ticker (use one with
  rich bronze coverage)
- `--day 2024-06-10` — pin a specific trading day
- `--out-json preflight.json` — structured report for CI

### Step 2 — Initial full backfill (~hours, overnight)

Once preflight is green, kick off the multi-hour `--full` run:

```bash
# Default: seed universe × from 2021-01-04 → yesterday.
poetry run python scripts/run_silver_ohlcv_build.py --full \
    --out-json full_backfill.json

# Recommended after G1: dynamic universe (SEED ∪ active watchlists).
poetry run python scripts/run_silver_ohlcv_build.py --full \
    --symbols active \
    --out-json full_backfill.json
```

Wall-clock estimate:
- ~100 symbols × ~1300 trading days × per-slice ~0.5s ≈ **18-25
  hours single-threaded**. Plan for an overnight + next-day window.
- I/O bound on Iceberg scans, not on CH inserts. Future enhancement
  could parallelize per-symbol via a semaphore (see
  `silver_layer_plan.md §3` — single-process today).

Recommended runtime setting:
- Run from a screen / tmux session so a terminal hiccup doesn't
  kill the job.
- Tail the log to spot failures: `tail -f logs/app.log | grep silver_ohlcv_build`.

**Status during the run:** the CLI logs `run_id` + per-slice
progress. The `ingestion_runs` CH table gets one row per
`build_window` invocation. The Postgres / S3 / Glue side is
**append-only** — partial runs are safe to interrupt; re-running
the same window is idempotent (PyIceberg upsert on the identifier).

### Step 3 — Verify (~1 minute)

After the backfill completes, audit the result:

```bash
# Default: 7-day verification window × SEED_SYMBOLS.
poetry run python scripts/verify_silver_build.py \
    --since 2024-01-01 --until $(date +%Y-%m-%d) \
    --out-json verify.json
```

The script aggregates findings across four phases:

1. **Coverage**: zero-actual-bar weekdays (suspect — should always
   have ≥1 bar on a trading day)
2. **Quality**: gap-count + max-gap-minutes outliers (threshold
   configurable via `--max-gap-count` / `--max-gap-minutes`)
3. **Disagreement**: any (symbol, date) where providers disagreed
   on close beyond tolerance (50¢ OR 0.5%)
4. **Audit**: `ingestion_runs` CH row count + status distribution
5. **Cross-check sample**: N random (symbol, date) cells passed
   through `SilverOhlcvReader.get_bars` — bars sorted, unique
   timestamps, valid provider tags, OHLC populated

**Pass criterion:** `✅ No issues found`. Exit 0.

**Issues found:** Exit 2 with a list of cells to investigate.
Common causes:

- **Many gap-count outliers, single day**: bronze ingest for that
  day was incomplete (operator action: re-run nightly polygon /
  schwab for that day, then re-run silver_ohlcv_build for that day)
- **Disagreement_count > 0 for one symbol, many days**: likely a
  provider feed issue (operator action: spot-check the (symbol, ts)
  cells via `GET /api/silver/bars/...` and compare to provider
  REST directly)
- **`zero_actual_bar_cells` on trading days**: bronze coverage gap
  for that symbol (operator action: check seed_universe membership,
  re-run nightlies, etc.)

### Step 4 — Enable the nightly loop

Once the full backfill + verification pass, enable the nightly loop:

```bash
# In .env:
SILVER_OHLCV_BUILD_ENABLED=true
SILVER_OHLCV_BUILD_RUN_HOUR_UTC=23
SILVER_OHLCV_BUILD_SYMBOLS=active   # G1 dynamic universe
```

Restart the FastAPI process. The lifespan starts the nightly loop,
which sleeps until 23:00 UTC and then runs yesterday × universe.
You'll see in the logs:

```
nightly_silver_ohlcv_build: loop armed (run hour 23:00 UTC)
nightly_silver_ohlcv_build: sleeping XXXXs until next run
```

### Step 5 — Spot-check Yahoo adjusted-close (10 random symbols)

Silver stores split-adjusted OHLCV directly — silver's `close` IS
Yahoo's `adjclose`. So this becomes a trivial 1-to-1 comparison.

```bash
# Example: NVDA 2024-06-10 10-for-1 split.
# Silver close at 14:30 ET on 2024-06-07 (Friday before split):
curl -sS "http://localhost:8000/api/silver/bars/NVDA?start=2024-06-07T18:30:00Z&end=2024-06-07T18:31:00Z" | jq '.bars[0].close'

# Expected: ~120.88 (matches Yahoo Finance's adjclose for that day).
# Silver `close` should be within $0.01 of Yahoo.
```

If divergences exceed $0.01, file a ticket — the per-provider
normalization math may have a corner case the canary tests missed.

## Re-running individual phases

The build is idempotent, so any of these is safe to re-run:

```bash
# Rebuild one (symbol, day) — useful for spot-fixing one cell:
poetry run python scripts/run_silver_ohlcv_build.py \
    --since 2024-06-10 --until 2024-06-10 --symbols NVDA

# Rebuild one symbol's entire history:
poetry run python scripts/run_silver_ohlcv_build.py \
    --full --symbols NVDA

# Rebuild a month for the whole universe:
poetry run python scripts/run_silver_ohlcv_build.py \
    --since 2024-06-01 --until 2024-06-30 --symbols active
```

PyIceberg's upsert on `(symbol, ts)` / `(symbol, date)` means the
on-disk silver rows end up byte-identical to the first run (modulo
`ingestion_ts` + `run_id`).

## Cancelling a run-in-progress

Ctrl-C is safe. The orchestrator processes slices sequentially —
the slice in flight completes (the running `upsert` returns) and
the loop exits with the slices it had already finished. Re-running
the same `--since`/`--until` picks up where it left off (idempotent).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Preflight ❌ on **catalog_reachable** | Bad `STOCK_LAKE_BUCKET` / AWS creds | Check `.env` + IAM. See [data_platform_plan.md](data_platform_plan.md) §IAM. |
| Preflight ❌ on **bronze_minute_tables** | Bronze never populated | Run nightly Polygon + Schwab once first. |
| Preflight 🟡 on **silver_corp_actions** | Corp-actions never ingested | `scripts/run_corp_actions_backfill.py --full` first. Silver build will run with F=1 otherwise (no adjustment applied). |
| Many `gap_count` outliers, same date | Bronze ingest partial for that date | Re-run nightly bronze for that date; re-run silver for that date. |
| `disagreement_count` consistently high on one symbol | One provider's feed is off | Verify via `/api/lake/bars` against each provider directly. |
| Nightly loop logs `not started — gated` | Env flag off | Set `SILVER_OHLCV_BUILD_ENABLED=true`. |

## Why TA-5.1.7 is a separate step

Steps 1–6 of TA-5.1 are pure code (LANDED 2026-05-17). Step 7 is
the **operator-go-live** moment: it flips the production toggle and
seeds silver from bronze for the first time. That's an irreversible
action (well, reversible by wiping silver and re-running, but still
expensive). Splitting it out keeps the code commits surgical and
the operator action explicit.

After TA-5.1.7 lands cleanly, the natural follow-ons are TA-5.3
(silver→CH backfill + tip-fill — the cockpit "warming up" UX) and
TA-5.5 (delete legacy Path ② + CH wipe-and-rebuild from silver).
