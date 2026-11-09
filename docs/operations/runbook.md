# Operator runbook

Day-to-day procedures for running the platform. Architecture context is in
[`README.md`](../architecture/README.md); provider-specific procedures (token refresh, adding a
provider) are in [`providers.md`](../architecture/providers.md).

## Conventions

Set these once before running anything in this runbook:

```bash
export AWS_PROFILE=stock-lake                     # required for all lake/S3 access
export STOCK_LAKE_BUCKET=<your-lake-bucket-name>
```

`settings.stock_lake_bucket` reads `STOCK_LAKE_BUCKET` directly, so shell and
Python env must agree. A **blank** ambient `AWS_PROFILE` makes every lake call
hang 45-90s — `config.py`'s `_normalize_aws_env` guards this, but export it
anyway.

---

## 1. Live tier

### Restart uvicorn (most common operation)

```bash
pkill -TERM -f 'uvicorn app.main_api'
sleep 3
poetry run uvicorn app.main_api:app --reload --port 8000 --host 127.0.0.1
```

Verify:

```bash
curl -s localhost:8000/api/v1/stream/status | jq '{started, provider, streaming_count}'
```

Expect `started=true`, the configured provider, and `streaming_count > 0`.

**When a restart is actually required:** `.env` changes, startup migrations,
provider switches, and a refreshed Schwab token (the process holds the old one
in memory). Python edits under `--reload` do not need one — verify with curl
instead.

### Auth-enabled local development

Do **not** start the auth-enabled backend with a bare
`poetry run uvicorn app.main_api:app --reload`. It works for charts, lake and
futures, but auth breaks silently:

- `app/config.py` defaults `AUTH_COOKIE_SECURE=true`, and browsers refuse
  `Secure` cookies over plain `http://localhost` — the session + CSRF cookies
  are dropped, so logout, session revoke, MFA enrollment and billing checkout
  all fail with *"Your security token is missing."*
- `COGNITO_CLIENT_SECRET` is not checked into `.env` (live AWS secret), so the
  login exchange fails outright without it.

Always use:

```bash
bash scripts/dev/run_auth_api.sh
```

### Add a symbol to the streaming universe

UI: `/app/stream` → "+ Add ticker". API:

```bash
curl -X POST localhost:8000/api/v1/stream \
  -H 'Content-Type: application/json' \
  -d '{"symbol": "PG", "notes": "added by ops"}'
```

Within ~5s the uvicorn log should show the warmup chain
(`Stream warmup tip-fill: PG fetched=N …`), then 1-min bars appear in CH.

Adding a symbol/sector/theme we **track** means adding its constituents to
`stream_universe` — not just backfilling CH. See
[`../standards/data/symbol_lifecycle.md`](../standards/data/symbol_lifecycle.md).

### Remove a symbol

```bash
curl -X DELETE localhost:8000/api/v1/stream/PG
```

The universe is **sticky**: removing a symbol from a watchlist does *not*
remove it from streaming. Only this endpoint does.

### Trigger a scheduled job manually

UI: `/app/status` → Scheduled jobs → ▶. API:

```bash
curl -X POST localhost:8000/api/v1/jobs/backfill_gap_sweeper/run
```

| Response | Meaning |
|---|---|
| `{"status":"started"}` | running in background; watch `/api/v1/jobs` for `last_success` |
| `{"status":"already_running"}` | wait for the current run |
| `{"status":"not_found"}` | name typo |

A manual run and the scheduled loop record identically to
`stocks.ingestion_runs`, so the health page history covers both.

### Check bar flow

```bash
curl -s localhost:8000/api/v1/stream/status | jq '.streaming_count, .streaming_symbols'
```

```sql
-- bars received in the last 5 minutes
SELECT source, count() AS bars, uniqExact(symbol) AS uniq
FROM ohlcv_1m
WHERE timestamp > now() - INTERVAL 5 MINUTE
GROUP BY source ORDER BY bars DESC
```

During market hours expect 100+ symbols on `source = "schwab-live"`.

### Exactly one process may own the options snapshot loop

`OPTIONS_SNAPSHOT_ENABLED` must be true in **one** process only. Measured
2026-08-12: with the loop live on both `:8000` and `:8001`, concurrent Iceberg
appends hit optimistic concurrency (`branch main has changed`) and **all 40
symbols failed on both sides with `rows_written=0`** — including the raw
payload archive, which is the recovery source. Two writers is strictly worse
than one.

Today `:8000` runs from the primary checkout and owns the loop. Every
worktree's `.claude/launch.json` ships `OPTIONS_SNAPSHOT_ENABLED=false` for
this reason. Adding a second server means disabling it everywhere else
**first**, then enabling it in one place.

**Option-chain history only accrues while something is running.** Unlike bars,
it cannot be backfilled — `/chains` returns only the *current* chain, so a day
not snapshotted is gone permanently. Whichever process owns the flag should be
running whenever the market is.

### A healthy stream does not mean Schwab auth is healthy

The live streamer authenticates once at connect and stays up, so `ohlcv_1m`
can look perfectly healthy while **every REST call fails**. Options collection
died this way for three months (2026-08 → 2026-11): the loop ran every 15
minutes and failed every time on an expired refresh token, while bars kept
flowing. Restarting does not fix it — only re-authorization does.

Two traps in the recovery, both hit in practice:

1. **`SCHWAB_REFRESH_TOKEN` in `.env` overrides the token file.** Re-auth
   writes `data/.schwab_refresh_token`, but a stale `.env` value silently wins.
   Update or unset it.
2. **Each worktree has its own `.env` and `data/`.** Re-authorizing in the
   primary checkout does nothing for a worktree — it keeps failing with the old
   token and looks like the re-auth did not work. Copy
   `data/.schwab_refresh_token` and the `.env` line across.

### Coverage is not uniform — do not resample 1m into daily

Schwab streams **300** equity symbols for this account, a hard account-level
ceiling on `CHART_EQUITY`, while `/stream/status` reports every subscribed
symbol as subscribed. Chunking the subscription does not raise it (tested).

So `ohlcv_1m` covers 300 symbols, not the whole universe. `ohlcv_daily` covers
415 because the daily path uses REST, which is not capped. **Resampling 1m into
daily silently loses 115 symbols.** Filed as `schwab-300-symbol-account-cap`
in [`../ISSUES.md`](../ISSUES.md).

---

## 2. Backfills

| Script | Source → target |
|---|---|
| `scripts/lake_import_athena.py` | existing S3 cache → `equities.polygon_raw` (one-time bulk) |
| `scripts/polygon_history_backfill.py` | Polygon flat-files → `equities.polygon_raw` |
| `scripts/schwab_history_backfill.py` | Schwab REST → `equities.schwab_universe` |
| `scripts/schwab_daily_refresh.py` | Schwab REST daily → `equities.schwab_daily_raw` |
| `scripts/futures_history_backfill.py` | Schwab REST → `futures.schwab_futures` |
| `scripts/futures_daily_backfill.py` | Schwab REST → `futures.schwab_futures_daily` |
| `scripts/polygon_futures_mirror.py` | Polygon flat-files → `polygon_flatfiles_mirror/` |
| `scripts/polygon_futures_parse_raw.py` | mirror → `futures.polygon_raw` |
| `scripts/polygon_futures_build_continuous.py` | `futures.polygon_raw` → `polygon_continuous` |
| `scripts/run_corp_actions_backfill.py` | Polygon REST → `equities.market_corp_actions` |
| `scripts/backfill_market_splits.py` | corp-actions → `equities.market_splits` |
| `scripts/rebuild_ch_from_lake.py` | **canonical** bulk lake → CH loader |
| `scripts/thetadata_backfill.py` | ThetaData EOD → `options_atm_iv_daily` |

Long backfills run in **CodeBuild** (`scripts/codebuild/buildspec_*.yml`), not
on the dev box.

> **Preflight anything over 5 minutes.** Log every outcome including zero, emit
> per-iteration completion markers, and verify mutations cross-side. See
> [`../standards/coding.md`](../standards/coding.md).

### Today's missing intraday bars

When the live stream missed the open, nightly jobs will **not** fix it — they
are prior-day only. Run the history backfill with `--start`/`--end` including
today, then reconcile CH from Schwab.

```bash
poetry run python scripts/schwab_history_backfill.py --start 2026-11-04 --end 2026-11-04
```

### Rebuild ClickHouse from the lake (disaster recovery / a new machine)

ClickHouse is a cache; the lake plus the nightly **state snapshot** in S3
(`s3://$STOCK_LAKE_BUCKET/ch_state/`) hold everything. One command rebuilds it:

```bash
poetry run python scripts/ch_bootstrap.py --dry-run             # the plan: snapshot, windows, steps
poetry run python scripts/ch_bootstrap.py --state-mode replace  # schema -> state -> bars -> futures -> daily -> verify
```

- **State** (stream universe, strategies, paper record, watchlists, run history,
  journal, options history) comes from the snapshot. `--state-mode replace`
  TRUNCATES any state table that already has rows, so use it on a fresh box and
  never on a ClickHouse holding newer data.
- **Bars** are rebuilt from the lake, each symbol over the window its recorded
  history needs (the 2006-era names start before the loader's 2021 default).
- **Verify** compares the result with the coverage recorded in the snapshot and
  exits 2 on any shortfall. The lake may hold more than the source ClickHouse did.
  Rows are counted as **distinct minutes**, not `count()` (duplicates inflated a laptop
  snapshot: AAPL "expected" 5.88M rows, more than the 5.03M minutes that exist). A snapshot
  taken *before* that change still carries inflated counts and can report false shortfalls;
  the next nightly snapshot replaces it. Counting is one query per symbol (about 160 s
  for 425 symbols); one `GROUP BY` exhausted ClickHouse's memory on the dev box.
- **Steps are independent.** `bars`, `futures` and `daily` need only the schema: a failed
  equity window no longer cancels the other loads (it did on 2026-11-06). A symbol with no
  lake rows in a window (a ticker that did not exist yet) is a *warning* when a sentinel
  read proves the lake is healthy; an unreadable lake still aborts the window.
- Wall-clock is dominated by the bar rebuild: ~76 s per heavy symbol on a
  2-vCPU box (AAPL, 1.07M rows). Measure the first batch before estimating.
- Do the same restore as a **rehearsal** into a scratch database:
  `scripts/ch_state_snapshot.py restore --target-database stocks_restore_test`.

**Before wiping or replacing any ClickHouse that has been collecting live bars,
archive what only it holds:**

```bash
poetry run python scripts/lake_archive_ch.py --family all           # dry run: what is missing
poetry run python scripts/lake_archive_ch.py --family all --apply   # append + verify from the lake side
```

Schwab's 1-minute history reaches back ~48 days, so a bar the live writer missed
cannot be re-fetched later. The nightly `lake_completeness_guard` job does this
for the last 14 days automatically and fails loudly if a write does not verify.
Check both jobs on the Health page (`lake_completeness_guard`, `ch_state_snapshot`).

### Recover a bad lake partition

```bash
# 1. Inspect snapshots
#    SELECT * FROM lake.equities.polygon_raw.snapshots ORDER BY committed_at DESC LIMIT 10
# 2. Soft-roll back
#    CALL lake.system.rollback_to_snapshot('lake.equities.polygon_raw', <snapshot_id>)
# 3. Re-pull the affected window with --force (bypasses the pre-scan, since the
#    table technically has rows there — they are stale)
```

The bad snapshot stays in metadata until `expire_snapshots` runs, so rollback
is reversible. Maintenance SQL and dialect rules:
[`lake_schema.md`](../architecture/lake_schema.md#6-maintenance).

---

## 3. Monitoring

### Daily

| Check | How | Healthy |
|---|---|---|
| API up | `curl localhost:8000/health` | `{"status":"ok"}` |
| Streaming | `curl localhost:8000/api/v1/stream/status` | `provider_ready=true`, `streaming_count > 0` |
| Bars flowing | CH 5-minute query above | >50 symbols during market hours |
| Job statuses | `curl localhost:8000/api/v1/jobs` | no `last_status="error"` |
| Job history | `/app/health` → last 10 runs | no repeated failures |
| CH disk | `du -sh /var/lib/clickhouse/` | < 50 GB |

### Weekly

| Check | How | Healthy |
|---|---|---|
| Iceberg file count | `aws s3 ls "s3://${STOCK_LAKE_BUCKET}/iceberg/equities/schwab_universe/data/" --recursive \| wc -l` | < 10,000 (else compact) |
| Iceberg metadata size | same path, `metadata/`, `--summarize` | < 1 GB |
| S3 total | `aws s3 ls "s3://${STOCK_LAKE_BUCKET}/" --recursive --summarize \| tail -2` | growth ~1 GB/month |

### Freeze symptoms → first suspect

| Symptom | Suspect |
|---|---|
| CH **and** lake both frozen on a recent date | Expired Schwab refresh token (~7d life). Re-auth, then restart uvicorn |
| One symbol fine, all others skip a day | Gap-detection single-symbol poison — a pre-loaded universe symbol moved `latest_loaded_date` |
| Symbols past a count threshold never stream | Schwab stream key cap; chunking does not raise it ([`../ISSUES.md`](../ISSUES.md)) |
| Every lake call hangs ~45-90s | Blank ambient `AWS_PROFILE` |
| Bulk write fails `BadDigest` only on full runs | S3 CRC64NVME multipart — set `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` before any AWS client init |

### Alerts worth wiring

- Stream disconnected >5 min (Status page shows it; add SNS).
- No bars in `ohlcv_1m` for >2 min during market hours.
- Glue catalog API errors.
- S3 cost spike.

---

## 4. Cost

```bash
aws s3 ls "s3://${STOCK_LAKE_BUCKET}/" --recursive --summarize | tail -2
```

Storage runs roughly $7-10/month for the whole lake (see
[`lake_schema.md`](../architecture/lake_schema.md)). Compute is CodeBuild-only and
usage-based; there is no standing cluster. ClickHouse is self-hosted.

---

## 5. Worktree safety

`git worktree remove --force` **deletes gitignored `data/`** — this wiped
strategy and paper state once. Check for untracked artifacts first and prefer
removal without `--force`. Strategy definitions and paper state are now durable
in ClickHouse (`strategy_definitions`, `paper_state`), but `data/` still holds
the Schwab refresh token and the universe seed CSV.
