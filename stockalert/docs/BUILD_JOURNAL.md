# Build Journal

Progress log for the StockAlert data platform + AI trading build. One
section per phase. A phase is **not done** until every checkbox is
ticked **and** the gate test is green.

Format conventions:
- `[ ]` open, `[x]` complete, `[~]` in progress, `[!]` blocked
- Date entries are UTC and use `YYYY-MM-DD`.
- "Gate" = the single objective test that must pass before starting the
  next phase.

---

## Phase 0 — Infrastructure foundation

**Goal:** PyIceberg can read/write a table in `s3://stock-lake-562741918372-us-east-1-an/`
via the AWS Glue catalog.

**Status:** ✅ COMPLETE
**Started:** 2026-05-14
**Completed:** 2026-05-14
**Gate:** `tests/integration/test_iceberg_connectivity.py` — 2/2 passed

### Tasks

#### Local code (no AWS access needed) — DONE
- [x] Build journal created
- [x] Add `pyiceberg` (with glue + s3fs extras) to Poetry deps
- [x] Add Iceberg/Glue settings to `app/config.py`
- [x] Update `.env.example` with Iceberg/Glue env vars
- [x] Create AWS provisioning script `scripts/provision_lake_infra.sh`
- [x] Create Iceberg catalog helper `app/services/iceberg_catalog.py`
- [x] Create connectivity test `tests/integration/test_iceberg_connectivity.py`
- [x] Pytest `integration` marker registered in `pyproject.toml`
- [x] `poetry lock` regenerated; `poetry check` passes
- [x] Test collection clean (429 tests; 3 pre-existing collection errors
      unrelated to Phase 0 — see Follow-ups below)
- [x] Full test suite excluding pre-existing failures green
      (416 passed, 6 pre-existing failures, 5 skipped — confirmed
      pre-existing via `ISSUES.md` and git history)
- [x] New gate test skips gracefully without AWS creds

#### Requires user (AWS-side) — DONE
- [x] AWS profile `stock-lake` configured (~/.aws/credentials)
- [x] IAM user `stock-lake-ingest` policy updated with bucket-config +
      Glue catalog perms
- [x] `.env` created from `.env.example`; `STOCK_LAKE_BUCKET=stock-lake-562741918372-us-east-1-an`,
      `AWS_PROFILE=stock-lake`
- [x] `poetry install` — pyiceberg 0.11.1 + extras installed
- [x] Provisioning script run successfully:
  - [x] S3 bucket `stock-lake-562741918372-us-east-1-an` (us-east-1, existing)
  - [x] Versioning enabled
  - [x] Public access blocked
  - [x] SSE-S3 encryption default
  - [x] Lifecycle rules applied (bronze/silver/gold tiering + multipart abort)
  - [x] Glue database `stock_lake` created
- [x] IAM policy attached (see Decision log entry 2026-05-14)

### Gate

```bash
poetry run pytest tests/integration/test_iceberg_connectivity.py -v
```

Must:
1. Connect to Glue catalog with bucket warehouse path
2. Create temp table `stock_lake.connectivity_check_<timestamp>`
3. Write one row via PyArrow → append
4. Read the row back (asserts symbol=TEST, value=42)
5. Drop the table cleanly

Test skips automatically if `STOCK_LAKE_BUCKET` is unset or no AWS
credentials are discoverable.

### Hand-off steps (user)

```bash
# 1. AWS credentials — pick one
aws configure                                # IAM user (simplest)
# OR: aws sso login --profile <profile>      # SSO
# OR: export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...

# 2. Sanity check
aws sts get-caller-identity

# 3. Create .env from template + set bucket
cp .env.example .env
# edit: STOCK_LAKE_BUCKET=stock-lake, STOCK_LAKE_REGION=us-east-1

# 4. Pull pyiceberg into the venv
poetry install

# 5. Provision bucket + Glue (idempotent, safe to re-run)
scripts/provision_lake_infra.sh

# 6. Run the Phase 0 gate
poetry run pytest tests/integration/test_iceberg_connectivity.py -v
```

When the gate is green, tick the remaining boxes above and mark
Phase 0 status complete.

### Notes
- 2026-05-14: AWS CLI installed (v2.34.45) but no creds configured;
  `.env` absent. Proceeded with local code work; AWS-side handed off.
- 2026-05-14: `pyiceberg` pinned at `>=0.7` with extras `[glue, s3fs,
  pyarrow]`. Lockfile regenerated.
- 2026-05-14: Gate run produced two transient S3 orphan dirs from
  early failed attempts (PyArrow nullability mismatch). Fixed test to
  declare `nullable=False` on the inbound PyArrow schema and added an
  explicit S3 purge in the `finally` block so subsequent runs are
  clean. Orphans removed manually.
- 2026-05-14: IAM policy includes `s3:PutLifecycleConfiguration` (used
  by provisioner) but not `s3:GetLifecycleConfiguration` — confirmed
  during a read-back attempt. Not blocking; add when we need to read
  lifecycle config programmatically.

### Follow-ups (not Phase 0 blockers)
- **Pre-existing test collection errors** in `tests/test_alert_flow.py`,
  `tests/test_indicators.py`, `tests/test_websocket.py` — reference
  modules that have never existed in the repo (`app.services.alert_service`
  etc.). Date back to the initial commit `ab6e71d`. Spawn a cleanup
  later.
- **Pre-existing failures** in `tests/test_schwab_provider.py` (5
  failures) and `tests/test_watchlist_repo.py::test_watchlists_containing`
  (1 failure). Schwab failures match `schwab-chart-fields-test-drift` +
  `schwab-streamer-url-key-test-drift` already tracked in `ISSUES.md`.
  Watchlist failure not yet diagnosed.

---

## Phase 1 — Bronze on Iceberg

**Goal:** Replace the existing `raw/provider=*/...parquet` lake with
Iceberg tables `bronze.{provider}_{kind}` registered to Glue. Existing
daily Parquets imported via `add_files` (no rewrites).

**Status:** ✅ COMPLETE
**Started:** 2026-05-14
**Completed:** 2026-05-14
**Gate:** `tests/integration/` — 6/6 passed

### Existing data to migrate (inventoried 2026-05-14)

```
s3://stock-lake-562741918372-us-east-1-an/raw/
  provider=polygon-flatfiles/kind=minute/
    year=2021/ ... year=2026/      1,325 daily Parquets, 5+ years
```

No `polygon-flatfiles/kind=day/`, no Schwab, no Alpaca data in the lake
today — those providers only flow into ClickHouse. The bronze layer
starts with one populated table (`bronze.polygon_minute`) and empty
table shells for the others; Phase 2 starts populating them.

### Tasks
- [x] Inspect a sample Parquet to confirm schema (columns, types,
      timestamps)
- [x] Create `bronze.polygon_minute` (partition `month(timestamp)`,
      sort `(symbol, timestamp)`, target 256 MB)
- [ ] Create remaining bronze tables (deferred until their first writer):
  - [ ] `bronze.polygon_day`
  - [ ] `bronze.schwab_minute`
  - [ ] `bronze.schwab_day`
  - [ ] `bronze.alpaca_minute`
- [x] Server-side import via Athena (replaces the planned `add_files`
      flow): 2,116,310,315 rows landed in `bronze.polygon_minute`
- [x] Row-count parity check vs source (exact, after deliberate NULL
      filter)
- [x] `BronzeIcebergSink` ([app/services/bronze/sink.py](../app/services/bronze/sink.py))
      replaces `LakeSink` as the canonical writer
- [x] Existing nightly archive job
      ([nightly_lake_refresh.py](../app/services/nightly_lake_refresh.py))
      switched to `BronzeIcebergSink`
- [x] Compaction at import time: 65 files at 547 MB avg, no further
      compaction needed for historical data
- [x] **Monthly compaction CLI**:
      [scripts/compact_bronze_monthly.py](../scripts/compact_bronze_monthly.py).
      Refuses to touch months > 90 days old without `--force` (Glacier
      IR minimum-storage protection).
- [x] Pytest integration tests
      ([tests/integration/test_bronze_sink.py](../tests/integration/test_bronze_sink.py)):
      4 cases — happy path, NULL-symbol filter, unsupported provider skip,
      empty frame skip. All against a real temp Iceberg table.

### Gate
- [x] Row-count parity: source 2,116,390,512 = bronze 2,116,310,315 +
      80,197 filtered NULL-symbol rows. Exact.
- [x] File-count check: **65 files** (target was < 200).
- [x] Sample query gate: 5y AAPL bars query, **3.78 s cold**
      (target was < 10 s).
- [x] Pytest integration tests, 6/6 passing:
  - `test_catalog_lists_configured_namespace`
  - `test_iceberg_table_roundtrip`
  - `test_bronze_sink_writes_and_reads_back`
  - `test_bronze_sink_drops_null_symbol_rows`
  - `test_bronze_sink_unsupported_provider_skips`
  - `test_bronze_sink_empty_frame_skips`

### Deferred items (carry forward)

- ~~**Nightly auto-catchup for missed days.**~~ **DONE 2026-05-16.**
  Both `refresh_polygon_lake_yesterday()` and
  `refresh_schwab_bronze_yesterday()` now auto-catch-up:
  - Helper `app/services/bronze/gaps.py` exposes
    `latest_bronze_date` (ET-basis trading day, not UTC),
    `missing_weekdays`, and `yesterday_et`.
  - With `target=None`, both refresh functions query their bronze
    table, compute the missing-weekday window, and loop. Cold-start
    fallback seeds yesterday only.
  - Unit tests: `tests/test_bronze_gaps.py` (8/8 pass).
  - Bug caught + fixed during build: UTC date misclassifies
    after-hours bars; switched to ET-basis trading-day reckoning.

- **Automate monthly compaction.** Today
  [scripts/compact_bronze_monthly.py](../scripts/compact_bronze_monthly.py)
  is manual. Right way to schedule is an asyncio background loop in
  `app/main_api.py` (same pattern as `nightly_lake_refresh.run_lake_refresh_loop`),
  gated by a `BRONZE_COMPACTION_ENABLED` env var, runs on the first
  Sunday of each month at 09:00 UTC, targets the just-closed prior
  month only. Skipped at user's request for Phase 1 — revisit when
  daily file accumulation starts hurting query latency (probably
  after 3–6 months of daily appends without a manual run).

### Risks
- The current S3 provider tag is `polygon-flatfiles`, not `polygon`.
  Decided to migrate into `bronze.polygon_minute` (flat files ARE
  Polygon's authoritative SIP data) but verify the data contents match
  what live Polygon WS would produce before treating them as
  interchangeable in silver provider-precedence rules.
- Compaction rewrites file layout; expect a one-time write surge equal
  to the dataset size (~few GB). Run during off-hours.

### Results (2026-05-14, historical-import phase)

Pivoted from a laptop-side `add_files` approach to **Athena server-side
INSERT** mid-execution after discovering the home-internet bandwidth
bottleneck (8 hours estimated vs 28 minutes via Athena). The pivot
required:
1. IAM updates adding scoped Athena actions on workgroup `primary`.
2. Two SQL-dialect corrections (Hive backticks in DDL, Trino double
   quotes in DML — the same query can't use both).
3. A partition-spec fix (the source files use `date=YYYY-MM-DD.parquet`
   as a *filename*, not a sub-directory, so `year` is the only real
   partition; `date` info is inside the Parquet).
4. Dropping the global `ORDER BY symbol, timestamp` from the INSERT —
   global sort on 2.1B rows pushed past Athena's 30-min query timeout.
5. Filtering NULL-symbol rows at the boundary.

Final import metrics:

| Metric | Value |
|---|---|
| Source rows | 2,116,390,512 |
| NULL-symbol rows filtered (data-quality) | 80,197 (0.0038%) |
| Bronze rows | 2,116,310,315 (= source − filtered, exact) |
| Data files | **65** (one per month, avg 547 MB) |
| Total compressed size | 35.6 GB |
| Athena INSERT wall time | 28 min |
| Athena scan | 33 GB |
| Cost | **$0.16** |

Gate-criterion queries (Athena cold):

| Query | Wall time | Scanned |
|---|---|---|
| `count(*) WHERE symbol = 'AAPL'` (5y full history) | 3.78 s | 522 MB |
| `count(*) WHERE ts in 2024-03-15` (universe-wide) | 2.49 s | 54.6 MB |
| `count(*) WHERE symbol = 'SPY' AND ts in Q1 2024` | 1.83 s | **0.1 MB** |

The 0.1 MB scan on the range-filtered single-symbol query proves
partition pruning + row-group min/max skipping are both working. Athena's
INSERT writer (without ORDER BY) wrote files that happen to be
symbol-clustered enough that row-group stats can skip ~99% of file
content for per-symbol queries.

`OPTIMIZE … BIN_PACK` ran in 4 s as a no-op — Athena's INSERT respected
`write.target-file-size-bytes` and produced properly-sized files on the
first pass.

### Findings (2026-05-14)

**Inbound Parquet schema (consistent across 2021–2026, 3 samples):**

| Column | Arrow type | Notes |
|---|---|---|
| symbol | large_string | non-null in data; nullable in metadata |
| timestamp | timestamp[ns, tz=UTC] | minute-granular, includes pre+after-hours |
| open / high / low / close | double | |
| volume | double | fractional in newer files (fractional shares) |
| vwap | double | always 0.0 — placeholder; not populated by flat files |
| trade_count | int64 | |
| source | large_string | always `"polygon-flatfiles"` |
| `__index_level_0__` | int64 | 🚫 pandas write artifact — strip on import |

**Row counts for one trading day** (full-market snapshot): 1.5M–1.9M.
~9,000–11,500 distinct symbols, growing over time.

**API spot-check vs flat files** (5 symbols × 2026-05-12, Polygon REST
v2 aggregates):

| Symbol | API rows | Parquet rows | Status |
|---|---|---|---|
| AAPL | 909 | 909 | exact match |
| MSFT | 883 | 883 | exact match |
| NVDA | 959 | 959 | exact match |
| SPY | 918 | 918 | 7 pre-market `close` drifts (±$0.01–$0.02) |
| TSLA | 955 | 955 | exact match |

SPY's 7 mismatches all occur **pre-market only** (08:15–12:53 UTC, before
13:30 UTC NYSE open). Open/high/low/volume are bit-identical; only the
last-print-per-minute close shifts by a cent on thin pre-market trades.
This is standard consolidator-edge behavior between flat-file batch
aggregation and live REST aggregation. **Regular-hours data is exact.**
Acceptable for bronze (which stores "what the provider delivered");
silver's `sources_seen` + provider precedence rules will surface this
class of drift later.

---

## Phase 2 — Schwab as a second bronze provider

**Goal:** Stand up `bronze.schwab_minute` with the last 48 days of 1-minute
bars from Schwab REST, plus a nightly job that keeps it fresh. Bronze now
has two providers feeding the same canonical schema, setting up Phase 3
(silver curation) to do its job.

**Status:** ✅ COMPLETE
**Started:** 2026-05-14
**Completed:** 2026-05-15
**Gate:** all integration tests green (9/9), 1.72M rows in
`bronze.schwab_minute`, Polygon-vs-Schwab agreement queryable.

### Decisions locked at phase start

- **Universe:** seed 100 (same list Polygon has, enables apples-to-apples
  comparison). Expandable later by re-running the backfill — idempotent.
- **Nightly cadence:** 22:00 UTC (= 3 PM Arizona, ~30 min after NYSE
  close). Polygon's nightly stays at 07:00 UTC.
- **Source tag:** `source="schwab"` literal in bronze rows. If a future
  Schwab live-streaming → bronze path is added, it'll use
  `source="schwab-stream"` to distinguish.
- **Sink design:** generalize `BronzeIcebergSink` to be table-agnostic
  (constructor takes the target Iceberg `Table` + an `accepted_providers`
  set) and add factory methods `for_polygon_minute()` and
  `for_schwab_minute()`. Avoids parallel classes per provider.
- **`schwab_lake_backfill.py` (writes to legacy `raw/`) stays** for now,
  but won't be used by any new code path. Marked for removal in Phase 6.

### Tasks
- [x] Add `bronze.schwab_minute` schema/partition/sort to
      [schemas.py](../app/services/bronze/schemas.py)
- [x] Add `ensure_bronze_schwab_minute()` to
      [tables.py](../app/services/bronze/tables.py)
- [x] Refactor `BronzeIcebergSink` to accept any bronze table; add
      `for_polygon_minute()` and `for_schwab_minute()` factories
- [x] New CLI [scripts/schwab_bronze_backfill.py](../scripts/schwab_bronze_backfill.py)
      — per-symbol REST pulls, rate-limited, weekend-aware
- [x] New service
      [app/services/nightly_schwab_refresh.py](../app/services/nightly_schwab_refresh.py)
      — daily asyncio background loop mirroring `nightly_lake_refresh`
- [x] Wired into [main_api.py](../app/main_api.py) startup behind
      `SCHWAB_NIGHTLY_ENABLED` env var
- [x] Integration test
      [tests/integration/test_schwab_bronze.py](../tests/integration/test_schwab_bronze.py)
      — 3 cases (write/read, provider filter, NULL filter)
- [x] Ran 48-day seed-100 backfill: **1,719,925 rows** across 33 trading
      days, 100 symbols
- [x] Athena verification queries (results below)

### Gate
- [x] 33 trading days × 100 symbols, **1,719,925 rows** in
      `bronze.schwab_minute`
- [x] Per-(symbol, day) bar counts within expected range:
      p05=386, **median=483**, p95=770. 97% of symbol-days in the
      350–800 normal range; 3% below (low-volume edge symbols or
      partial-data days).
- [x] Pytest integration suite: **9/9 passing**
      (3 new Schwab + 4 polygon + 2 connectivity)
- [x] Athena `SELECT count(*) FROM stock_lake.schwab_minute` works
      (3.2 s, 1.1 MB scanned)
- [x] Polygon-vs-Schwab agreement query produces meaningful baseline:
      AAPL last 30 days, 14,952 shared minutes, **98.6% exact close
      agreement**, 0.5% within-penny drift, 0.9% diverged (≥$0.01)
- [ ] Nightly background loop verified live (gated by user setting
      `SCHWAB_NIGHTLY_ENABLED=true` and restarting the server)

### Results (2026-05-15)

| Metric | Value |
|---|---|
| Total rows | 1,719,925 |
| Trading days covered | 33 |
| Distinct symbols | 100 (full seed-100) |
| First bar | 2026-03-30 11:00 UTC |
| Last bar | 2026-05-14 23:59 UTC |
| Empty/error days | 1 (2026-04-03 — Schwab API quirk, see Risks) |
| Weekend skips | 14 |
| On-disk files | 33 (one per day, pre-compaction) |
| Total compressed size | 37.7 MB |
| Backfill wall time | 33 min |

### Polygon vs Schwab agreement (AAPL, last 30 days)

| Bucket | Bars | Share |
|---|---|---|
| Same minute in both | 14,952 | — |
| Close prices exact (Δ<$0.001) | 14,747 | **98.6%** |
| Close within penny ($0.001–$0.01) | 76 | 0.5% |
| Close diverged (≥$0.01) | 129 | 0.9% |

The 1.4% disagreement is concentrated in pre/after-hours bars (consistent
with what we saw in Phase 1's SPY drift analysis). This is the kind of
data that silver-layer curation in Phase 3 will use to pick a canonical
provider per bar.

### Known issues / follow-ups

1. **2026-04-03 missing entirely.** Schwab API returned HTTP 400 for
   every symbol on that date with the message
   `"Enddate ... is before startDate"`. This is a quirk in how
   `historical_df` combines `period=1` with explicit `startDate`/`endDate`
   on certain dates. The production live-streaming path is unaffected
   (it uses streaming, not pricehistory). To recover the missing day,
   fix the period-vs-explicit-window interaction in
   [schwab_provider.py:732 historical_df](../app/providers/schwab_provider.py),
   then re-run the backfill with `--start 2026-04-03 --end 2026-04-03`.
   Out-of-scope for Phase 2 (silver dedup handles single-provider gaps
   gracefully).

2. **No compaction yet.** 33 small files (~1 MB avg) in
   `bronze.schwab_minute`. After a few weeks of daily appends, a
   monthly compaction will be helpful. The existing
   `scripts/compact_bronze_monthly.py` is hard-coded to
   `polygon_minute` — extend to take a `--table` argument when we
   automate compaction (already on the deferred list).


## Pre-Phase 3 — Code organization & MCP scaffold

**Goal:** Get the codebase into a shape that supports the silver/gold
build AND agent (MCP) access cleanly. Three sub-steps; each its own commit.

**Status:** Step 1 complete; Steps 2 & 3 pending.

### Step 1 — Service folder reorg + startup isolation ✅ (commit `5b0655d`, 2026-05-16)

- `app/services/` grouped into domain folders:
  - `bronze/`   — Iceberg tables + sink (pre-existing)
  - `ingest/`   — nightly_polygon_refresh, nightly_schwab_refresh,
                  backfill_service, flatfiles_backfill, historical_loader,
                  sinks (Sink Protocol + ClickHouseSink + SinkResult)
  - `live/`     — watchlist_service, monitor_service, monitor_manager
  - `journal/`  — journal_sync (Schwab-only), journal_parser, pnl
  - `legacy/`   — lake_archive, lake_sink, s3_lake_client (Phase 7 removal)
- Renamed `nightly_lake_refresh.py` → `ingest/nightly_polygon_refresh.py`
  to match `nightly_schwab_refresh.py`.
- Split `flatfiles_sinks.py`: generic Sink + ClickHouseSink to
  `ingest/sinks.py`; legacy LakeSink to `legacy/lake_sink.py`.
- Each domain folder gets its own README.md per doc-discipline memory.
- `main_api.py` startup hardened with `_safe_start()`: every subsystem
  starts in isolation. Journal sync failing no longer blocks watchlist,
  nightly bronze, or HTTP routes. Foundation tasks (CH schema, batcher)
  remain non-isolated by design.
- `routes_market.py` docstring corrected — banner is provider-agnostic.
- 38 files updated for imports; 144/144 in-scope tests pass.

### Step 2 — Read services + CH-independent lake routes (NOT STARTED)

**Why:** Today routes mostly read directly from ClickHouse, and lake
reads happen inline via PyIceberg. To support (a) agents reading the
lake without CH up, and (b) MCP tools as thin wrappers around services,
we need explicit read services with Pydantic contracts.

- [ ] `app/services/readers/bar_reader.py` — CH `ohlcv_1m` reads
      (`get_recent_bars`, `get_bars_in_range`)
- [ ] `app/services/readers/signal_reader.py` — CH signals reads
      (`get_recent_signals`, `get_signals_by_symbol`)
- [ ] `app/services/readers/quote_service.py` — provider-quote abstraction
      (works against any provider with `get_quotes`; same fallback chain
      as the banner already uses)
- [ ] `app/services/readers/bronze_reader.py` — Iceberg lake reads
      via PyIceberg + DuckDB (`get_bronze_bars`, `list_symbols`,
      `latest_trading_day`). **Critical: this is the "works when CH is
      down" path** for agent historical access.
- [ ] Refactor existing routes to call the new readers (thin adapters).
- [ ] New routes `app/api/routes_lake.py` — `/api/lake/bars`,
      `/api/lake/symbols`, `/api/lake/last-day`. CH-free path.
- [ ] Pydantic schemas for each reader's input/output (the contract
      MCP tools will reuse).
- [ ] Integration tests against real CH + real bronze.

**Gate:** `/api/lake/bars?symbol=AAPL&start=...&end=...` returns rows
with ClickHouse stopped. Existing CH-backed routes still work normally.

### Step 3 — MCP scaffold mounted on FastAPI (NOT STARTED)

- [ ] `app/mcp/` package with FastMCP server
- [ ] Mount at `/mcp` on the existing FastAPI app (single process)
- [ ] One MCP tool per read service: `get_bronze_bars`, `get_recent_bars`,
      `get_recent_signals`, `get_quote`, `list_symbols`, `gap_report`
- [ ] Tools call the same services that routes call — zero business
      logic in the tool layer
- [ ] `app/mcp/README.md` documenting the tool surface

**Gate:** Claude / an LLM agent can call `get_bronze_bars` via MCP and
get a DataFrame back. End-to-end "agent reads lake" works.

### What "done" looks like before Phase 3 (Silver)

Sign-off criteria for moving on:

1. All tests green
2. `/api/lake/bars` route returns data when CH is stopped
3. MCP `/mcp` endpoint responds to a `list_tools` call
4. Every service has a README + sits in the right domain folder
5. Legacy raw/ writer is in `legacy/` and marked for removal

---

## Phase 3 — Silver layer + corp actions

**Goal:** Build `silver.ohlcv_1m` as the canonical, deduped, gap-filled
read source for backtests and ML training. Two providers in bronze
(polygon + schwab) get merged with provider-precedence rules.

**Status:** not started. Depends on Pre-Phase 3 steps 2 + 3 being done.

Detail in [data_platform_plan.md §6](data_platform_plan.md).

---

## Phase 4 — Live → Bronze (CH 5-min flush)

**Status:** not started. Deferred — nightly bronze ingest is currently
keeping bronze fresh within T+1 which is sufficient for backtests and
training. Add when sub-day freshness in bronze becomes a real need.

---

## Phase 5 — Reader flip (dashboard reads bronze for history)

**Status:** not started. The Pre-Phase-3 Step 2 work (`BronzeReader`
service + `/api/lake/bars` route) lays the foundation; this phase
flips the dashboard's chart endpoint to prefer bronze for older data
and CH only for today's live bars.

---

## Phase 6 — Gold + ML reproducibility

**Status:** not started.

---

## Phase 7 — Retire legacy lake prefix

**Status:** not started. Triggered once Phase 3 (silver) + Phase 5
(reader flip) are stable and the `s3://.../raw/` data is provably
unreferenced.

---

## Backlog — pick up whenever

Items that aren't blocked by anything and aren't on the critical path.
Tackle when there's a natural window. Add to the top as new ones come up.

### Schwab API — full pass-through coverage

**Goal:** every Schwab Market Data + Trader API endpoint is reachable
through our system — both for HTTP routes and (once Step 3 lands) for
MCP tools so agents can use them. "Pass-through" = the user / agent can
do anything they could do hitting Schwab's API directly, but with our
auth handling, retries, and canonical response shapes.

**Currently wrapped** (in [app/providers/schwab_provider.py](../app/providers/schwab_provider.py)):

| Endpoint | Method | Used by |
|---|---|---|
| `/pricehistory` | `historical_df` | nightly_schwab_refresh, backfill, agent training |
| `/quotes` | `get_quotes` | banner, dashboard |
| `/chains` | `get_option_chains` | (provider has it; not yet routed) |
| `/expirationchain` | `get_expiration_chain` | (not yet routed) |
| `/movers/{symbol_id}` | `get_movers` | routes_movers |
| `/markets` | `get_market_hours` | (not yet routed) |
| `/accounts` | journal_sync | journal pages |
| Streaming (CHART_EQUITY, LEVELONE_*) | SchwabStreamer | live watchlist |

**Gaps to close:**

- [ ] **Options data — full ingest path.** `get_option_chains` exists
      on the provider but there's no service that snapshots option
      chains into ClickHouse or bronze. Open questions:
  - What schema? `option_chains` table keyed on
    `(underlying, expiration, strike, call_or_put, snapshot_ts)`.
  - Live snapshots or daily end-of-day? Both?
  - Retention / partition — options blow up row counts fast
    (~hundreds of strikes × dozens of expirations per underlying).
  - Bronze table per provider (`bronze.schwab_option_snapshot`) +
    eventual silver merge.
- [ ] **Option price history.** Schwab supports historical option
      pricing for specific contracts. No wrapper yet.
- [ ] **Option streaming.** Schwab Streamer has option services
      (`LEVELONE_OPTIONS`, `OPTION`). Not subscribed today.
- [ ] **Trader API — order placement.** `POST /accounts/{hash}/orders`
      and cancel. Required for Trading-AI Phase 8 (paper trading) and
      Phase 9 (live).
- [ ] **Order status + history.** `GET /accounts/{hash}/orders` for
      replay / reconciliation.
- [ ] **Instruments search.** `GET /instruments?symbol=...` for
      symbol resolution and instrument-type lookup.
- [ ] **Transactions.** Already pulled by journal_sync but expose as
      a service + MCP tool for ad-hoc agent queries.
- [ ] **Streaming for futures** (`CHART_FUTURES`, `LEVELONE_FUTURES`).
      Partial support exists; no consumer wires it up.

**MCP tool surface to add** (after Step 3 scaffold lands):

```
get_option_chain(symbol, expiration?, strike_range?)
get_option_quote(option_symbol)
list_option_expirations(symbol)
get_account_positions()                ← read-only, safe for agents
get_recent_transactions(account_hash, days=30)
place_order(...)                       ← gated behind execution service
cancel_order(order_id)
```

**Design rule.** Each Schwab endpoint becomes:
1. A method on `SchwabProvider` (already mostly done — keep going).
2. A wrapper service in `app/services/` if it needs caching /
   business logic on top.
3. An optional FastAPI route in `app/api/` for human/UI use.
4. An MCP tool in `app/mcp/tools/` for agent use.

Each layer is thin — the service has the logic, route + MCP tool are
adapters. Same pattern as the rest of the codebase.

### Other backlog items

- [ ] **Bronze compaction automation** — scheduled `compact_bronze_monthly.py`
      via asyncio background task or external cron. Currently manual.
      (See Phase 1 deferred items.)
- [ ] **`/health/services` JSON endpoint** — per-subsystem status,
      so dashboards / monitoring don't have to parse logs.
      (See STARTUP_FLOW.md proposed improvements.)
- [ ] **Tier-grouped startup logs** — `[HOT]` / `[COLD]` / `[OPS]`
      prefixes for clearer ops view.
- [ ] **Cleaner soft-fail logging** when provider creds are missing —
      single WARNING instead of ERROR + WARNING + ✅.

---

## Trading AI track (parallel)

See [trading-ai-build-plan.md](trading-ai-build-plan.md) Phases 1–9.
Cannot start before Phase 0 of the data platform is green.

---

## Decision log

Persistent decisions made during the build. New decisions append at the
bottom with a date.

- **2026-05-14** — Single S3 bucket `stock-lake`, separate by prefix.
  One bucket per env (dev/prod) if/when we add staging environments.
- **2026-05-14** — Glue Data Catalog over self-hosted Iceberg REST
  catalog. Re-evaluate when we need data branching (Nessie).
- **2026-05-14** — Separate bronze table per provider (not a single
  table with provider partition). Schemas drift.
- **2026-05-14** — Bars only. No tick/quote data.
- **2026-05-14** — Polygon = corp-actions source of truth. Silver
  carries both raw and adjusted price columns.
- **2026-05-14** — Iceberg snapshot pinning is mandatory for every
  saved model. `model_training_runs` registry in ClickHouse.
- **2026-05-14** — Watermark ledger (`lake_archive_watermarks`)
  becomes `ingestion_runs` — audit/ops only. Iceberg `MERGE INTO`
  is the correctness layer.
- **2026-05-14** — Bucket is `stock-lake-562741918372-us-east-1-an`
  (existing, not the literal `stock-lake` name in earlier plans —
  that name is globally taken). All plan docs reference the name via
  `${STOCK_LAKE_BUCKET}` so this doesn't require doc rewrites.
- **2026-05-14** — IAM user `stock-lake-ingest` (account 562741918372).
  Policy covers bucket-config + scoped Glue access to database
  `stock_lake` only. Production agent containers will get a separate,
  tighter role per service (ingest write-only, silver-builder
  read/write, evaluator read-only).
- **2026-05-14** — Existing S3 data at
  `raw/provider=polygon-flatfiles/kind=minute/` (1,325 files,
  2021-2026) will be `add_files`-imported into `bronze.polygon_minute`
  during Phase 1. Treated as `polygon` provider in silver precedence —
  Polygon flat files are Polygon's authoritative SIP data.
- **2026-05-14** — Lifecycle policy loosened: bronze IA transition
  pushed 60d → 180d; silver IA transition removed (silver stays
  Standard indefinitely). Reason: Polygon data is expensive to re-
  acquire (no ongoing subscription planned), and storage cost is
  pennies at our scale. Compaction will target only the last 90 days
  to avoid early-deletion fees against IA/Glacier IR minimum-storage
  durations. Re-tighten when bronze exceeds 500 GB or monthly bill
  warrants.
- **2026-05-14** — Doc discipline (now in
  [docs/README.md working agreement](README.md)): every microservice
  folder has a `README.md` (what it does, owns, contract, how to
  test). Docs in `docs/` get updated in the same change as the code
  that prompts them, not later. Drift breaks the pick-up-where-we-
  left-off promise.
- **2026-05-14** — Historical import switched from `add_files` to
  Athena server-side INSERT. Reason: laptop-side rewrite estimated at
  8 hours (home-bandwidth-bound); Athena finished in 28 min for $0.16.
  Athena workgroup `primary` (engine v3). DDL must use Hive
  backticks; DML must use Trino double quotes. The same query can't
  use both styles. Athena INSERT writer respects
  `write.target-file-size-bytes` natively, so OPTIMIZE after a
  fresh INSERT is typically a no-op.
- **2026-05-14** — Data-quality finding: ~80k Polygon flat-file rows
  (0.0038% of 2.1B) have NULL symbol. They have valid OHLCV values
  but no ticker → unusable. Filtered at the bronze boundary
  (`WHERE symbol IS NOT NULL AND "timestamp" IS NOT NULL`). The
  silver-layer `bar_quality` job should track this rate ongoing.
- **2026-05-14** — `BronzeIcebergSink` uses `append`, not `overwrite`.
  Rationale: PyIceberg's `overwrite(filter=...)` reads existing files
  to determine which rows to delete. On a 35-GB table that's hundreds
  of MB of I/O per call — unacceptable for a daily nightly cadence or
  any future live writer. Idempotency moves UPSTREAM: nightly job has
  its watermark; future live writer has a "last-flushed-ts" cursor.
  If duplicates ever do occur, silver's provider precedence + dedup
  handles them at silver-build time.
- **2026-05-14** — Athena DELETE with partition + symbol filter is
  the maintenance tool for cleaning bronze (vs PyIceberg's `delete`
  which scans the whole table). Used for test-row cleanup; available
  for operator use.
- **2026-05-15** — `BronzeIcebergSink` refactored to be
  table-agnostic. One class accepts a target `Table` + an
  `accepted_providers: set[(provider, kind)]` filter. Factories
  `for_polygon_minute()` and `for_schwab_minute()` cover the
  common cases. Avoids parallel per-provider sink classes; new
  bronze tables get a factory method, not a new class.
- **2026-05-15** — Schwab REST pricehistory does not return `vwap`
  or `trade_count`. Bronze schema keeps those columns nullable so
  the same canonical shape is shared across providers; silver's
  precedence rules treat both providers uniformly without
  conditional schemas.
- **2026-05-15** — Schwab's pricehistory API has a known quirk
  where passing `period` together with explicit `startDate`/`endDate`
  can yield HTTP 400 ("Enddate is before startDate"). Cost us
  2026-04-03 in this Phase 2 backfill. Fix deferred — silver
  dedup handles single-provider single-day gaps gracefully; can
  re-pull the day after fixing `historical_df`.
- **2026-05-16** — Service folders grouped by **domain, not provider**.
  `app/services/` split into `bronze/`, `ingest/`, `live/`, `journal/`,
  `legacy/`. Rationale: a provider is a configuration parameter (set
  via `STREAM_PROVIDER` / `HISTORY_PROVIDER`), not a service boundary.
  Bronze is multi-provider via factory methods; ingest jobs are
  per-provider but share the `Sink` Protocol. Provider-grouped folders
  would fight the architecture and force duplicate code for
  multi-provider services like watchlist + bronze.
- **2026-05-16** — Startup uses `_safe_start()` so each subsystem can
  fail independently. Journal sync expiring its Schwab token, or
  watchlist failing to subscribe, does not block nightly bronze
  ingest, HTTP routes, or other subsystems. Foundation tasks
  (CH schema init, OHLCV batch writer) stay non-isolated by design —
  if those fail the app has nothing useful to serve.
- **2026-05-16** — Journal service formally scoped Schwab-only.
  README in `app/services/journal/` documents this: account/trade
  data only makes sense from the broker where trades execute. If a
  future user uses a different broker, journal would become a
  multi-provider service with provider-specific implementations;
  not in scope today.
- **2026-05-16** — Alpaca deferred indefinitely. Code paths remain
  (provider abstraction supports it) but Alpaca creds are not
  exercised. Provider switching is `DATA_PROVIDER` env-only.
- **2026-05-16** — MCP server will be **mounted on the existing
  FastAPI app at `/mcp`** rather than run as a separate process.
  Single process for local dev simplicity; split out later if/when
  we run the trading-AI services in their own containers. Same
  services power both HTTP routes (humans/UI) and MCP tools (agents).
- **2026-05-16** — Lake reads must work without ClickHouse. Agents
  doing historical/training reads should hit `BronzeReader` → S3 +
  Iceberg directly; CH is for live/recent only. This decouples
  agent capability from CH availability. New routes
  (`/api/lake/*`) and MCP tools route to bronze without touching CH.
- **2026-05-16** — Nightly auto-catchup. Both `refresh_polygon_lake_yesterday`
  and `refresh_schwab_bronze_yesterday` now detect gaps and fill them
  on each run, using a new helper `app/services/bronze/gaps.py`
  (`latest_bronze_date` + `missing_weekdays`). Critical implementation
  detail: gap-detection uses **Eastern-time trading-day**, not UTC date.
  Polygon flat-files for "May 14 trading day" include after-hours bars
  whose UTC timestamp falls on May 15; using UTC date would falsely
  advance the "latest day" counter and skip the May 15 trading day on
  the next nightly run. ET-date matches flat-file naming + Schwab's
  pricehistory windows. 8 unit tests cover the date math; existing
  integration tests still pass.
- **2026-05-15** — Renamed Polygon-nightly env vars to match the
  Schwab pattern (`<PROVIDER>_NIGHTLY_*`):
  `LAKE_ARCHIVE_ENABLED` → `POLYGON_NIGHTLY_ENABLED`,
  `LAKE_ARCHIVE_RUN_HOUR_UTC` → `POLYGON_NIGHTLY_RUN_HOUR_UTC`,
  `NIGHTLY_LAKE_SYMBOLS` → `POLYGON_NIGHTLY_SYMBOLS`,
  `NIGHTLY_LAKE_KIND` → `POLYGON_NIGHTLY_KIND`. Reason: the old
  names predated the bronze-Iceberg migration (when there was only
  one "lake archive" — Polygon's raw S3 dump) and were inconsistent
  with the per-provider pattern. Settings attributes renamed
  identically. Internal symbols like the helper function
  `resolve_nightly_lake_symbols` and the module file
  `nightly_lake_refresh.py` kept their names — they're module
  internals, not the inconsistency the user flagged. Hard rename
  with no backward-compat shim (single-user codebase; .env files
  updated in the same change).
- **2026-05-14** — Write cadence vs partition layout decoupled.
  Writer cadence stays daily (one append per trading day's Polygon
  flat file; the nightly job and live 5-min flush both write
  incrementally). Iceberg `month(ts)` partitioning + post-write
  compaction is what produces the monthly on-disk layout — not a
  monthly writer. Phase 1 makes compaction a first-class task, not
  maintenance. Avoids the "wait until month-end to write" trap that
  breaks incremental ingest.
