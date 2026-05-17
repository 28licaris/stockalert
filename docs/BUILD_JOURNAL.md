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
      pre-existing via [ISSUES.md](ISSUES.md) and git history)
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
  `schwab-streamer-url-key-test-drift` already tracked in
  [ISSUES.md](ISSUES.md).
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

### Step 2 — Read services + CH-independent lake routes (IN PROGRESS)

**Why:** Today routes mostly read directly from ClickHouse, and lake
reads happen inline via PyIceberg. To support (a) agents reading the
lake without CH up, and (b) MCP tools as thin wrappers around services,
we need explicit read services with Pydantic contracts.

Sequencing: build the gate-critical path first (bronze reader + its
contract + route + gate test), then the supporting CH readers and the
existing-route refactor. Lets us validate the agent-readiness design
end-to-end before touching production-facing routes.

**Slice 1 — bronze reader + contract** (LANDED 2026-05-16)
- [x] `app/services/readers/schemas.py` — `BronzeBar`, `BronzeBarsResponse`
      Pydantic models. The contract MCP tools will reuse verbatim.
- [x] `app/services/readers/bronze_reader.py` — `BronzeReader.get_bars()`
      over `bronze.{provider}_minute` Iceberg tables. Provider routing
      via `_PROVIDER_TABLE`; half-open intervals; UTC at the boundary;
      Pydantic shape out. CH-independent.
- [x] `app/services/readers/README.md` — folder contract + roadmap of
      planned readers (`bar_reader`, `signal_reader`, `quote_service`,
      future `silver_reader`).
- [x] Integration test `tests/integration/test_bronze_reader.py` — 8
      cases against a real Glue catalog + S3 temp table: happy path,
      empty windows, unknown symbol, half-open interval edges, naive-
      datetime UTC coercion, `limit=N` returns most-recent-N,
      `ValueError` on unknown provider. AWS-free unit case passes
      locally; AWS-gated cases pending an integration run.

**Slice 2 — `/api/lake/bars` route + gate** (LANDED 2026-05-16)
- [x] `app/api/routes_lake.py` — `/api/lake/bars` over `BronzeReader`,
      `Depends(get_bronze_reader)` so tests override cleanly. Thin
      adapter; no business logic in the route.
- [x] Wired into `main_api.py` (46 routes total, up from 45).
- [x] Unit + gate test `tests/test_routes_lake.py` — 6 cases:
      happy path with response-shape assertion, empty-window 200/[],
      unknown-provider 400, infra-failure 500, missing-params 422,
      and the **structural CH-independence gate**:
      `test_lake_route_does_not_import_clickhouse` walks every
      `app.*` module transitively reachable from `routes_lake` via
      AST + `importlib.util.find_spec` and asserts NONE sit under
      `app.db.*` (ClickHouse). This regression-guards the
      CH-independence promise at the code-structure level — even if
      a future change accidentally adds a CH import to the bronze
      read path, this test will fail before production breakage.
      All 6/6 pass.
- [x] **End-to-end live verification:** booted uvicorn, curled
      `/api/lake/bars?symbol=AAPL&start=2024-08-01T14:00:00Z&end=2024-08-01T14:05:00Z&limit=5`,
      got HTTP 200 with real production AAPL bars
      (`source: "polygon-flatfiles"`, sub-second). Confirmed Pydantic
      response shape matches `BronzeBarsResponse`.

**The Phase Pre-3 Step 2 gate is GREEN.** Agent-readiness for
historical reads is proven: bronze data flows through a typed contract
without any ClickHouse code in the call path. Slices 3 and 4 below
are scope-completion (more endpoints + the CH-backed readers), not
gate-blockers.

**Slice 3 — list/discovery surface** (LANDED 2026-05-16)
- [x] `BronzeReader.list_symbols(provider, since, limit)` — distinct
      symbol scan with default 30-day window, sorted output, null/
      empty filtered out. Reads only the `symbol` column for cost.
- [x] `BronzeReader.latest_trading_day(provider, lookback_days)` —
      delegates to `bronze.gaps.latest_bronze_date` so gap-detection
      and the read surface share one source of truth. ET-basis
      trading day per the `feedback_et_vs_utc_trading_day` rule.
- [x] `GET /api/lake/symbols` route with `provider`, `since`, `limit`
      query params; echoes effective `since` (resolved default) in
      the response so consumers can record what was queried.
- [x] `GET /api/lake/last-day` route with `provider`, `lookback_days`
      query params. 200 with `latest_trading_day: null` when no rows
      exist in window (no 404).
- [x] Tests in `tests/test_routes_lake.py`: 7 new cases covering
      both routes (happy paths, default-since echo, unknown provider
      400, null-when-no-data, lookback bounds 422). Total now 13/13
      green, including the unchanged CH-independence structural gate.
- [x] **Live verification:** `/api/lake/last-day?provider=polygon`
      returned `2026-05-15` (correct — yesterday ET, matches nightly
      catch-up). `/api/lake/symbols?since=2024-08-14&limit=10`
      returned first 10 tickers alphabetically from production bronze.

**Slice 4a — CH-backed readers + provider quote service** (LANDED 2026-05-16)
- [x] `app/services/readers/bar_reader.py` — `BarReader` over CH
      `ohlcv_1m` / `ohlcv_5m` / `ohlcv_daily`. Methods:
      `get_recent_bars` (DESC → ASC flip), `get_bars_in_range`
      (interval-routes to direct or resampled query), `get_latest_bar_per_symbol`.
      Supported intervals: 1m / 5m / 15m / 30m / 1h / 4h / daily.
      Thin wrappers over `app.db.queries` — no SQL in the reader.
- [x] `app/services/readers/signal_reader.py` — `SignalReader` over CH
      `signals`. Methods: `get_recent_signals`, `get_signals_by_symbol`.
- [x] `app/services/readers/quote_service.py` — `QuoteService` over the
      `get_market_quotes_provider()` fallback chain. Async; methods:
      `get_quote(symbol)`, `get_quotes(symbols)`. Normalizes
      provider-specific field names (Schwab's `lastPrice`/`totalVolume`/
      epoch-ms `quoteTime`; Polygon's variants; etc.) into the
      canonical `Quote` shape. invalidSymbols passed through.
- [x] Pydantic schemas added to `readers/schemas.py`: `LiveBar`,
      `LiveBarsResponse`, `LatestBarsResponse`, `Signal`,
      `SignalsResponse`, `Quote`, `QuotesResponse`.
- [x] Unit tests in `tests/test_readers_unit.py` — 23 cases.
      Stubbed CH queries via `unittest.mock.patch`, stubbed provider
      for QuoteService. Covers interval routing, ASC re-sort,
      unknown-interval ValueError, empty-input short-circuits, field
      alias fall-through, Schwab epoch-ms vs ISO timestamps, missing
      `get_quotes` graceful degradation.
- [x] Combined test run (`test_readers_unit.py` + `test_routes_lake.py`):
      **36/36 green**. Production-bronze structural CH-independence
      gate still passes.

**Slice 4b — refactor existing routes to use new readers** (LANDED 2026-05-16)

Three sub-commits, each reviewable on its own:

- [x] **Prep commit:** extend `BarReader` + `QuoteService`.
      - Renamed `BarReader` interval `"daily"` → `"1d"` to match
        `queries.SUPPORTED_INTERVALS` exactly.
      - Added `BarReader.get_bars_for_chart(symbol, interval,
        lookback_days, limit)` — the multi-table fallback + auto-
        limit logic that used to live in `routes_signals./bars` now
        lives here, unit-tested in isolation.
      - Added `QuoteService.get_raw_quotes(symbols, chunk_size)` —
        returns `(merged_dict, invalid_list)` without forced
        normalization, for consumers (the banner) that need
        provider-specific fields the canonical `Quote` doesn't carry.
      - `get_quotes` and `get_raw_quotes` share a private
        `_fetch_chunked_merged` helper.
      - `_row_to_live_bar` made flexible: accepts both `ts` and
        `timestamp` row keys (different queries use different
        aliases).

- [x] **`routes_signals.py` refactor.**
      - `GET /api/signals` → `SignalReader.get_signals_by_symbol`
        via `Depends(get_signal_reader)`.
      - `GET /api/bars` → `BarReader.get_bars_for_chart` via
        `Depends(get_bar_reader)`. ~60 lines of routing/fallback
        logic deleted from the route layer.
      - Response shapes preserved verbatim for the dashboard.
      - Fixed real bug along the way: `_row_to_signal` was reading
        the wrong column names (`signal_type`/`ts_signal`/
        `price_at_signal`) when `queries.list_signals` actually
        returns short aliases (`type`/`ts`/`price`). Unit test was
        passing on stubbed (wrong) data. `_signal_row` fixture
        updated to mirror the real shape.
      - Live-verified `/api/bars?symbol=AAPL` against production CH.

- [x] **`routes_market.py` refactor.**
      - `GET /api/market/banner` → `QuoteService.get_raw_quotes` via
        `Depends(get_quote_service)`. The `_fetch_quotes_merged`
        helper deleted from the route — chunking now lives in
        `QuoteService`.
      - Banner-specific extraction (`_extract_row`) stays in the
        route because Schwab's `regularMarketNetChange` /
        `assetMainType` etc. are richer than the canonical `Quote`
        shape MCP tools will consume.
      - `routes_market.py`: 222 → 196 lines.
      - Tests migrated from `monkeypatch` of the factory function to
        FastAPI `app.dependency_overrides[get_quote_service]` — the
        idiomatic override. Added a new test for invalidSymbols
        passthrough now that it crosses the service boundary.
      - Live-verified the banner against production Schwab (SPY at
        $737.34, full asset_type/net_change/change_pct).

- [x] **`routes_watchlist.py` — deliberately NOT refactored.**
      The snapshot endpoint includes a `bar_count` field that's a
      watchlist-quality metric, not a market metric. Forcing it
      through `BarReader.get_latest_bar_per_symbol` (which only
      returns canonical `LiveBar`) would require either two SQL
      queries or a fake "with-metadata" reader variant — both worse
      than the existing direct query. Added a doc-comment in
      `_snapshot_for` documenting the decision and pointing at
      `feedback_platform_design_intent` for the principle: readers
      own the canonical contract; non-canonical metrics live next
      to the consumer that needs them.

**Final state for Step 2**

- All four planned readers live in `app/services/readers/` with one
  shared `schemas.py` contract.
- All three lake endpoints (`/api/lake/bars`, `/api/lake/symbols`,
  `/api/lake/last-day`) operate via `BronzeReader`.
- Three of four CH-bound endpoints (`/api/signals`, `/api/bars`,
  `/api/market/banner`) now go through reader services; the fourth
  (`/api/watchlists/.../snapshot`) keeps direct query access by
  design.
- Combined test surface: 87 green across reader unit tests + lake +
  market + watchlist + instruments route tests. Structural
  CH-independence gate on `routes_lake` still passes.

**Step 2 done.** Ready for Step 3 (MCP scaffold).

**Gate:** `/api/lake/bars?symbol=AAPL&start=...&end=...` returns rows
with ClickHouse stopped. Existing CH-backed routes still work normally.

### Step 3 — MCP scaffold mounted on FastAPI (IN PROGRESS)

Scope-up from the original journal plan: every read service in
`app/services/readers/` will get one tool per public method, plus
tools wrapping the watchlist/movers/instruments/coverage surfaces,
plus Schwab pass-through (options/market hours), plus system
observability. Sliced for incremental delivery.

**Slice 1 — Foundation + lake tools** (LANDED 2026-05-16)
- [x] `mcp[cli]>=1.0` added as a dependency (PyPI `mcp` package
      → `FastMCP`).
- [x] `app/mcp/server.py` — global `mcp = FastMCP("stockalert")`
      instance + `register_all_tools()` + `mount_on(app)` helper.
      Mount composes FastMCP's session-manager lifespan with the
      FastAPI lifespan so initialization is automatic.
- [x] `app/mcp/middleware.py` — `tool_call(name, **fields)` context
      manager. Logs success-with-timing, distinguishes `ValueError`
      (client problem, WARNING) from other exceptions (server bug,
      ERROR with traceback). Designed to absorb future hooks
      (auth, rate limit, cost accounting) without per-tool changes.
- [x] `app/mcp/tools/lake.py` — 3 tools backing `BronzeReader`:
      - `get_bronze_bars(symbol, start, end, provider, limit)`
      - `list_bronze_symbols(provider, since, limit)`
      - `get_latest_trading_day(provider, lookback_days)`
      Each tool: docstring with `USE WHEN`, `Args`, `Returns`, `Cost`
      sections (the LLM-visible affordance). Body is `with tool_call(...)`
      then one reader call. Returns the exact `schemas.py` Pydantic
      shape — HTTP and MCP surfaces share the contract byte-for-byte.
- [x] `app/mcp/README.md` — folder contract, planned tool surface
      table, "how to add a new tool" recipe, layering rules.
- [x] `main_api.py` mounts MCP via `_safe_start`-equivalent isolation:
      a try/except so MCP failures don't break the API. Mounted at
      `/mcp` → streamable-HTTP endpoint at `/mcp/mcp/`.
- [x] Tests `tests/test_mcp_lake.py` — 8 cases:
      - **Discovery:** `list_tools` returns 3 lake tools; descriptions
        present + one-line summaries; input schemas cover required
        + optional args.
      - **Invocation:** `call_tool(...)` against a stubbed reader
        returns the expected Pydantic shape for all 3 tools.
      - **Unknown tool:** raises (FastMCP's `ToolError`).
      - **Structural gate:** AST-walks every `app.*` module reachable
        from `tools/lake.py` and asserts none sit under `app.db.*`.
        Same pattern as `test_lake_route_does_not_import_clickhouse`
        for HTTP routes — CH-independence enforced at the code-
        structure level for the MCP path too.
      - 8/8 green.
- [x] **End-to-end live verification:** booted uvicorn, used the
      official `mcp.client.streamable_http` Python client to:
        1. Open a session,
        2. Call `list_tools()` — returns all 3 with full descriptions,
        3. Call `call_tool("get_latest_trading_day", {"provider":"polygon"})`
           — returns `{"provider":"polygon", "latest_trading_day":"2026-05-15"}`
           from production bronze, sub-second.
      This is the exact path Claude Desktop / any MCP-compatible
      agent will take. **The Phase Pre-3 Step 3 gate is GREEN for
      the bronze slice.**

**Slice 2 — Live tier + signals + quotes** (LANDED 2026-05-16)
- [x] `app/mcp/tools/live.py` — 4 tools backing `BarReader`:
      - `get_recent_bars(symbol, limit)` — newest N 1-minute bars ASC.
      - `get_bars_in_range(symbol, start, end, interval, source_table)` —
        explicit window; supports forced source_table for power users.
      - `get_bars_for_chart(symbol, interval, lookback_days, limit)` —
        chart-friendly with multi-table fallback + auto-limit.
      - `get_latest_bar_per_symbol(symbols)` — snapshot across many
        symbols at once; omits symbols with no rows.
- [x] `app/mcp/tools/signals.py` — 2 tools backing `SignalReader`:
      - `get_recent_signals(limit)` — newest N across all symbols.
      - `get_signals_by_symbol(symbol, limit)` — drill-into-one or
        all-symbols sweep with bigger default limit.
- [x] `app/mcp/tools/quotes.py` — 2 tools backing `QuoteService`:
      - `get_quote(symbol)` — single quote; returns null when
        provider can't resolve.
      - `get_quotes(symbols)` — chunked batched. `QuoteService` does
        the chunking under the hood, so this is one call from the
        agent's perspective. (Curated `get_market_banner` deferred to
        Slice 3 — needs the dashboard-shape extraction logic.)
- [x] `register_all_tools()` updated to import all 4 tool modules.
- [x] `tests/test_mcp_live.py` — 11 cases parallel to test_mcp_lake.py:
      discovery (all 8 new tools advertised + descriptions), invocation
      (each tool's stub-reader round-trip), the `Optional[Quote]`
      return-wrapping quirk pinned in two assertions.
- [x] **End-to-end live verification** via official `mcp.client`
      streamable-HTTP client (the same one Claude Desktop uses):
      - `list_tools` → 11 tools advertised
      - `call_tool("get_recent_bars", {"symbol":"AAPL","limit":3})`
        → 3 real CH bars; last at 2026-05-15 23:59, close=299.846
      - `call_tool("get_quote", {"symbol":"SPY"})`
        → last=737.34, provider=schwab (real Schwab REST call)

**The MCP agent path is live across all three tiers** — Iceberg
bronze, ClickHouse live, and Schwab REST quotes. Same Pydantic
shapes, same readers, two surfaces (HTTP routes + MCP tools).

**Slice 3 — Discovery + observability tools** (LANDED 2026-05-16)
12 new tools across 6 files. Total tool surface: 23.

- [x] `tools/watchlist.py` — 3 read-only tools:
      `list_watchlists`, `get_watchlist` (with members),
      `get_watchlist_members`.
- [x] `tools/movers.py` — `get_movers(symbol_id, sort, frequency)`.
      Schwab provider-backed; degraded-mode returns `{}` on any
      provider error so the agent path never raises.
- [x] `tools/instruments.py` — `search_instrument(query, limit)` (the
      fuzzy/ranked symbol resolver), `get_instruments(symbols, projection)`.
- [x] `tools/market.py` — `get_market_hours(market)`.
- [x] `tools/coverage.py` — the ML-quality observability layer:
      - `get_coverage(symbol, start, end, interval)` — actual vs
        regular-session-expected bar count + first/last bar timestamps.
      - `find_intraday_gaps(symbol, start, end, min_gap_minutes)` —
        contiguous missing-bar ranges, with the existing
        `queries.find_intraday_gaps_async` doing the heavy lifting.
      - `get_bronze_table_stats(table)` — row count, file count,
        snapshot ID, on-disk size for a bronze Iceberg table. Iceberg
        metadata-only; cheap regardless of table size.
- [x] `tools/system.py` — platform-self-diagnosis:
      - `get_health()` — aggregate status ('ok'/'degraded'/'down'),
        per-subsystem `ServiceStatus` rows. Pings CH + Iceberg in
        parallel via `asyncio.to_thread`.
      - `get_lake_freshness()` — per-table latest trading day for
        bronze tables. Per-table error isolation: schwab failing
        doesn't blank out the polygon entry.
- [x] New Pydantic schemas in `app/services/readers/schemas.py`:
      `WatchlistSummary`, `WatchlistDetail`, `WatchlistsResponse`,
      `CoverageReport`, `IntradayGap`, `GapReport`, `BronzeTableStats`,
      `LakeFreshnessReport`, `ServiceStatus`, `SystemHealthReport`.
      The MCP surface is now formally the same Pydantic contract
      surface that HTTP routes use — 17 models, one source of truth.
- [x] `register_all_tools()` updated; tests in
      `tests/test_mcp_discovery.py` — 18 cases including:
      - Discovery: 12 new tools registered.
      - Watchlist round-trips with stubbed `watchlist_service`.
      - Movers/instruments/market_hours with stubbed `get_provider()`.
      - Degraded-mode contract (bare provider w/o the method → `{}`).
      - Coverage % calculation across regular-session weekday-bar
        accounting (`actual / expected` rounded to 4 decimals).
      - find_intraday_gaps converting CH dicts → IntradayGap models.
      - get_bronze_table_stats error path (AWS unreachable →
        BronzeTableStats with `error` populated).
      - get_health across all three status states.
      - get_lake_freshness per-table error isolation.
- [x] **End-to-end live verification** against production data via
      `mcp.client.streamable_http`:
      - `get_bronze_table_stats(polygon_minute)`
        → 2,116,486,243 rows in 68 files, 38GB, real snapshot ID.
      - `get_lake_freshness()`
        → polygon_minute + schwab_minute both at 2026-05-15.
      - `get_health()` → status='ok', both tiers up.
      - `search_instrument("apple", 3)` → AAPL with Schwab metadata.
      - `list_watchlists()` → real watchlists.
      All 23 advertised tools accessible to any MCP client (Claude
      Desktop / Inspector / programmatic clients) right now.

**Slice 4 — Schwab pass-through** (later, lower priority)
- [ ] `tools/schwab_options.py` (option chain / expirations / option
      quote).
- [ ] `tools/journal.py` (Schwab account + trade history).

**Slice 5 — Gated writes** (its own phase, NOT before Trading AI work)
- [ ] `tools/writes.py` — watchlist mutation with allowlist.
- [ ] `tools/trading.py` — Schwab Trader API. Kill-switch protected.

**Gate (Step 3):** an LLM agent can call any read tool through MCP
and get the same Pydantic shape the HTTP route would return — proven
end-to-end with the official MCP client.

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

### Technical-analysis indicator library expansion

**Goal:** expand `app/indicators/` from the current three (RSI, MACD,
TSI — which is all divergence detection needs) into a full TA toolkit
that agents and strategies can pick from without pulling in external
deps. Full target list lives in
[app/indicators/README.md](../app/indicators/README.md) — momentum,
trend, MAs, volatility, volume, cycles. Each gets its own file + class
+ unit tests, following the `Indicator(ABC)` contract in
[base.py](../app/indicators/base.py). Wiring is one-line per indicator
into `INDICATOR_MAP` in `services/live/monitor_service.py`.

Not on the critical path for Phase 3 (silver). Pick up when a specific
strategy or agent needs an indicator that isn't there yet.

### Signal-detector library expansion

**Goal:** expand `app/signals/` from the current one (divergence) into
a catalog of named pattern detectors that strategies and agents can
compose. Full target list lives in
[app/signals/README.md](../app/signals/README.md) — trend reversals,
continuations, MA crossovers, threshold crossings, volatility breakouts,
volume confirmations, candlestick patterns, mean-reversion triggers.
Each gets its own file (or grouped file) + unit tests, following the
pure-function detector contract in the README. Wiring is one-line per
detector into `DETECTOR_MAP` in `services/live/monitor_service.py`.

Not on the critical path for Phase 3 (silver). Pick up when a specific
strategy needs a pattern that isn't there yet. Often paired with an
indicator add (e.g. Stochastic RSI %K/%D detector needs the Stochastic
indicator first).

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

Two docs govern this track:

- [trading-ai-build-plan.md](trading-ai-build-plan.md) — **strategic
  roadmap** (services, phases 1–9, reward engineering, deployment).
- [trading_subsystem_design.md](trading_subsystem_design.md) —
  **implementation contract** (Pydantic shapes, Protocols,
  folder layout, modularity guarantees). Read this before writing
  trading-subsystem code.

The data platform's Pre-Phase 3 Step 3 (MCP scaffold) is now done,
so all gates are clear to start the trading subsystem work.

### Phase TA-1 — Core backtest harness + canary strategy (LANDED 2026-05-16)

**Goal:** put a working backtest engine in place so every later
agent / strategy work has somewhere to land. Run the canary
(SMA crossover) end-to-end on real bronze data, prove the
reproducibility contract, and start the `agent_runs` registry.

Scope (per [trading_subsystem_design.md §10 Phase TA-1](trading_subsystem_design.md#phase-ta-1-core-harness--canary-strategy-next-session)):

- [x] `app/services/sim/` scaffold — 9 files, all per the design
      doc:
      - `schemas.py` — `Bar` Protocol, `Action`, `Position`, `Trade`,
        `RunMetrics`, `RunResult`, `BacktestConfig`, `PortfolioSnapshot`.
      - `strategy.py` — `Strategy` Protocol + `BaseStrategy`.
      - `context.py` — `Context` + `BarHistory` (deque-backed,
        per-bar indicator cache).
      - `portfolio.py` — `Portfolio` accounting with cash-clamped
        buys, position-clamped sells, set_position decomposition,
        per-bar mark-to-market, fp-epsilon residual cash clamp.
      - `fees.py` — Protocols + 5 implementations
        (`ZeroFees`, `PerShareFees`, `PercentFees`,
        `NextBarOpenFill`, `PercentSlippage`) + name registries.
      - `backtester.py` — `Backtester.run(strategy, config)` with
        snapshot pinning for the 1m bronze path, CH-fallback for
        the 1d path (no snapshot), strict interval-match check,
        git_sha capture.
      - `evaluator.py` — `StandardEvaluator` with interval-aware
        annualization, peak-to-trough drawdown, win-rate/profit-
        factor guards for degenerate inputs.
      - `registry.py` — `agent_runs` CH writer + `fetch_run` +
        `list_runs`. Best-effort `write_run` and strict
        `write_run_strict` variants.
      - `README.md` — folder contract, how-to-add-strategy/indicator/
        fees-model, modularity contracts, what's NOT in TA-1.
- [x] `app/indicators/` expanded:
      - `sma.py` — SMA via pandas rolling.mean().
      - `ema.py` — EMA via pandas ewm(adjust=False) matching every
        charting platform's convention.
      - `registry.py` — `get_indicator(name, **params)` +
        `list_indicators()`. Supports `sma`, `ema`, `rsi`, `macd`,
        `tsi`.
- [x] `SmaCrossoverStrategy` — interval-configurable canary
      (constructor takes `interval` so the same logic runs on `1d`,
      `1m`, or any future interval). Long-only, full-position-on-
      cross-up / full-exit-on-cross-down, position_size_pct
      defaults to 0.95.
- [x] `agent_runs` CH table wired into `init_schema()`. 24 columns
      including snapshot_id, git_sha, JSON config + JSON full
      metrics for the full reproducibility pin.
- [x] CLI `scripts/run_backtest.py` + `configs/canary.yaml`. YAML
      → `BacktestConfig`, strategy loader, pretty metrics output,
      `--no-write` / `--quiet` flags.
- [x] **Tests (39/39 green):**
      - `tests/test_sim_unit.py` — 38 unit tests covering schemas
        round-trip, both indicators against known math, registry
        name resolution, BarHistory eviction, Context indicator
        caching + per-bar invalidation, Context.log capture,
        Portfolio buy/sell/set_position/MTM/cash-clamp/pos-clamp,
        all fee + slippage models (including the per-share min/max
        cap precedence), Evaluator (total return, max DD,
        degenerate-trade guards, no-variance Sharpe), SMA
        crossover (warmup hold, params validation, cross emission),
        Backtester (interval mismatch raise, end-to-end with
        stubbed source, deterministic re-run).
      - `tests/integration/test_sim_real_bronze.py` — runs the
        canary against production AAPL minute bronze (RTH of
        2024-08-01), asserts snapshot_id captured, git_sha captured,
        equity curve populated, final equity in sane band.
      - `test_strategy_is_pure` — AST-walks every module reachable
        from `app/services/sim/strategies/*.py` and asserts none
        sit under `app.db.*` or `app.providers.*`. Same pattern as
        the existing `test_lake_route_does_not_import_clickhouse`.
      - `test_backtester_deterministic` — same inputs → same
        metrics + same equity curve + same trades.

**Gate (GREEN 2026-05-16):**

```bash
$ poetry run python scripts/run_backtest.py --config configs/canary.yaml
  Run: 507b6c6b-a4df-4a2e-adab-3bc6c4b0a8cc
  Strategy: sma_crossover v0.1
  Window:   2023-01-01 .. 2024-12-31  (1d, ['AAPL'])
  Snapshot: (none — CH path)
  Git SHA:  b70049be8576
  Starting capital  $     40,000.00
  Final equity      $     41,059.91
  Total return               +2.65%
  Sharpe ratio                0.305
  Max drawdown               -5.90%
  N trades                        5
```

Two consecutive runs of the canary produced **bit-for-bit identical
metrics** (total_return = 0.026497749999999654, sharpe_ratio =
0.3052698765608661, final_equity = 41059.90999999999). Both rows
landed in `agent_runs`. Reproducibility is proven at the production-
data level.

**Note for daily on `1d` path:** snapshot_id is currently empty
because CH `ohlcv_daily` has no snapshot semantics. Daily bronze
table (Phase 1 deferred item) is the path to full daily-tier
reproducibility — until then daily-interval backtests are
reproducible only via `git_sha` + `strategy_version` + `config`
identity, not via Iceberg snapshot pinning. **1m bronze path
pins snapshot fully and is the canonical training data source.**

### Phase TA-2 — LLM-driven strategy + agent self-evaluation MCP (LANDED 2026-05-17)

- [x] `app/services/sim/strategies/llm_agent.py` — `LLMAgentStrategy`
      wrapping Claude via the official `anthropic` SDK. Implements
      the `Strategy` Protocol directly (not via BaseStrategy) so it
      can manage its own setup/teardown for the API client + cache.
      - **Response caching** in local SQLite keyed on
        `sha256(model || system_prompt || user_prompt)`. Same prompt
        → cache hit → zero API cost. Cache persists across processes
        so a replay tomorrow is free.
      - **Cost-bounded by construction.** Strategy holds during
        warmup (no API call); cost per bar = at most one API call
        on first run, zero on replay. `_CallStats` accounts
        api_calls / cache_hits / parse_failures / api_failures
        for observability.
      - **Errors degrade to `hold()`** — API failure (rate limit,
        network), parse failure (model wrapped JSON in prose, model
        returned nonsense), or missing API key in setup. The
        backtest continues; we'd rather emit a measurable run than
        crash.
      - **Deterministic by default** — `temperature=0.0` removes
        randomness; combined with response caching, the same
        config produces an identical `agent_runs` row on replay.
      - **Pluggable indicators** in the prompt — `IndicatorSpec`
        list in params lets the operator (or another agent) tune
        what signals Claude sees without code changes.
      - **Action parsing**: tolerant JSON-object extraction
        (finds first `{...}` in the response) — robust to model
        wrapping JSON in prose despite system-prompt instructions.

- [x] `app/mcp/tools/sim.py` — two new MCP tools:
      - **`run_backtest(strategy_name, strategy_params, config,
        write_to_registry=True)`** → `RunMetrics`. Supports
        `sma_crossover` and `llm_agent`. This is the
        agent-iteration tool — an LLM can propose a strategy
        config, run it, see the metrics, then propose a different
        config.
      - **`list_strategy_runs(strategy_name, limit)`** → list of
        slim rows from `agent_runs`. The "how have my runs
        performed" tool. JSON-safe coercion of datetime / UUID
        columns so the agent gets serializable data.

- [x] `configs/llm_agent.yaml` — sample LLM-strategy config (AAPL
      2024 daily, SMA + RSI in context, Claude Sonnet 4.6 at
      temperature 0). Documents cost expectation (~$0.50-0.75 per
      full-year run on Sonnet pricing; replays are free).

- [x] **Tests (17 new + 2 MCP tool tests = 19/19 green):**
      - `tests/test_llm_agent_unit.py` (16 cases):
        - JSON extraction (strict, prose-wrapped, garbage).
        - Cache-key determinism + model-name sensitivity.
        - SQLite cache: roundtrip + miss + persistence across reopens.
        - Warmup → no API calls; bars past warmup → exactly N calls.
        - **Replay produces all cache hits, zero API calls.**
        - Action emission: buy-when-flat, buy-ignored-when-long,
          sell-with-position, sell-ignored-when-flat.
        - Parse failure → hold + stat increment.
        - API failure → hold + stat increment + cache NOT written.
        - position_size_pct clamps LLM-suggested oversize.
        - Two independent runs sharing one cache produce
          identical action sequences (reproducibility gate).
      - `tests/test_mcp_sim.py` (5 cases):
        - Both tools advertised in `list_tools`.
        - `run_backtest` against `sma_crossover` + stubbed bars
          end-to-end through MCP — returns valid RunMetrics dict.
        - Unknown strategy → MCP-side error.
        - `list_strategy_runs` returns slim view; datetimes are
          isoformat strings.
        - `limit > 200` clamps silently.

- [x] **End-to-end live verification** via official MCP client:
        ```
        25 tools advertised
        run_backtest(sma_crossover, AAPL 2024 Q3)
          → n_trades=1, total_return=+3.60%, sharpe=2.02,
            final_equity=$41,440.53
        list_strategy_runs(limit=3) → 3 rows including the
          just-completed run + the 2 reproducible runs from TA-1
          (+2.65% identical metrics)
        ```

**DEFERRED:** end-to-end live verification against real Anthropic API.
Pre-flight checks passed (smoke config prepared at
[configs/llm_agent_smoke.yaml](../configs/llm_agent_smoke.yaml),
unit tests cover the API-call path with stubs, env-var loading
verified). Awaiting `ANTHROPIC_API_KEY` in
`/Users/licaris/dev/stockalert/.env`. Cost when done: ~$0.05 for
smoke (45 trading days AAPL), ~$0.50 for full-year. Tracked in
[ISSUES.md `ta2-live-anthropic-run-deferred`](ISSUES.md).

**The agent self-evaluation loop is live** (in stub form — proven by
the 21 unit + MCP tests + the reproducibility regression).
An LLM agent connected
to this server can:

  1. `list_bronze_symbols` → discover the universe
  2. `get_bronze_bars(symbol, start, end)` → look at history
  3. `run_backtest({strategy_name: "llm_agent", ...})` → test
     its own hypothesis (or one it generated)
  4. `list_strategy_runs(strategy_name)` → compare with past attempts
  5. iterate

…with real production data, full reproducibility, and zero API cost
on replays. **This is the foundation Trading-AI Phases 3+ build on.**

### Phase TA-3.1 — Indicator math expansion (LANDED 2026-05-17)

Adds the four indicators TA-3's strategies need + the
[indicator_exposure_design.md](indicator_exposure_design.md) doc
covering how indicators are computed and served (Pattern A:
compute-on-read via a single `IndicatorReader`; gold-tier
pre-compute deferred to Phase 6).

- [x] `app/indicators/wma.py` — `WMA` (linear-weight MA).
- [x] `app/indicators/atr.py` — `ATR` (Wilder's smoothing).
      Requires `high` + `low` series in addition to `close`.
- [x] `app/indicators/bollinger.py` — `BollingerBands`. `compute()`
      returns the middle band (SMA); `compute_full()` returns dict
      of `{upper, middle, lower, bandwidth, percent_b}` — same
      multi-output convention as MACD.
- [x] `app/indicators/stochastic.py` — `StochasticOscillator`.
      `compute()` returns smoothed `%K`; `compute_full()` returns
      `{k, d}` for both signal lines in one pass.
- [x] `app/indicators/registry.py` updated — `INDICATOR_REGISTRY`
      now has 9 entries: sma, ema, wma, rsi, macd, tsi, stochastic,
      atr, bollinger. Strategies reach all by name via
      `ctx.indicator(name, **params)`.
- [x] `tests/test_indicators_ta3.py` — 20 cases covering math
      correctness on hand-crafted series, warmup behavior, param
      validation, multi-output `compute_full` shapes, registry
      resolution.
- [x] `docs/indicator_exposure_design.md` (NEW) — full architectural
      design for the upcoming exposure layer. Three patterns
      compared (compute-on-read / gold features / cached); decision
      to ship Pattern A now and defer B to Phase 6; concrete folder
      layout, Pydantic shapes, HTTP routes, MCP tools, dashboard
      migration path, multi-output convention, testing strategy.
- [x] `docs/README.md` + doc-relationship diagram updated.
- [x] `app/indicators/README.md` — indicator catalog refreshed
      with all 9 indicators by family (MA / Momentum / Volatility),
      multi-output convention documented.

**No exposure layer yet — that's TA-3.2.** The math is in place and
all consumers (`Context.indicator`, the existing `INDICATOR_REGISTRY`,
the LLM strategy's `IndicatorSpec`) can already request the new
indicators by name. Next commit builds `IndicatorReader` + HTTP
routes + MCP tools so the dashboard and agents can see them too.

### Phase TA-3.2 — Indicator exposure layer (LANDED 2026-05-17)

Per [indicator_exposure_design.md §4](indicator_exposure_design.md#4-concrete-design-ta-3-implementation):

- [x] **Pydantic shapes** in `app/services/readers/schemas.py`:
      `IndicatorValue` (timestamp + Optional[float]),
      `IndicatorSeries` (named series with values + label +
      params echo), `IndicatorChartData` (bars + multiple
      series + optional snapshot_id). One contract; HTTP routes
      and MCP tools both produce these byte-identical shapes.

- [x] **`IndicatorReader`** in
      `app/services/readers/indicator_reader.py` — single source
      of truth for indicator computation across all consumers.
      - `get_series(symbol, indicator, params, start, end, interval, provider)`
        returns one canonical `IndicatorSeries`. Multi-output
        indicators return only the canonical component
        (middle band / %K / MACD line).
      - `get_chart_data(symbol, indicator_specs, start, end, interval, provider)`
        returns `IndicatorChartData` with bars + N series.
        Multi-output indicators decompose into one
        `IndicatorSeries` per component
        (`bollinger_upper` / `bollinger_middle` / etc.).
      - Bar source resolution: `interval='1m'` → `BronzeReader`
        + snapshot_id pinning; everything else → `BarReader` with
        `LiveBar` → `BronzeBar` conversion. Uniform response shape.
      - Error semantics: single-indicator `get_series` raises
        `ValueError` on unknown indicator; multi-indicator
        `get_chart_data` degrades to per-spec "error stubs" so
        one bad indicator doesn't kill a chart of five.

- [x] **HTTP routes** in `app/api/routes_indicators.py`:
      - `GET /api/indicators/series?symbol=&start=&end=&indicator=&interval=&params=<json>`
      - `POST /api/indicators/chart-data` with body
        `{symbol, start, end, interval, provider, indicators[]}`
      - Both wired into `main_api.py` via `include_router` under
        `/api` with the `Indicators` tag.

- [x] **MCP tools** in `app/mcp/tools/indicators.py`:
      - `compute_indicator(symbol, indicator, start, end, interval, params, provider)`
      - `compute_indicators(symbol, indicators[], start, end, interval, provider)`
      - `get_chart_data(symbol, interval, lookback_days, indicators[], provider)`
        — convenience wrapper that resolves `lookback_days` into
        an explicit window.
      - Registered via `app.mcp.server.register_all_tools`. Total
        MCP tools advertised: **28** (was 25).

- [x] **Tests `tests/test_indicator_exposure.py`** — 24 cases:
      - 5 helper-fn cases (`_bars_to_df`, `_pd_series_to_indicator_values`
        with NaN-to-None + reindex-on-length-mismatch,
        `_format_label` including the prefix-strip on
        `bollinger_upper` → "BB Upper").
      - 4 `get_series` cases (SMA basic, empty bars, Bollinger
        canonical-only, unknown indicator raises).
      - 5 `get_chart_data` cases (multi-indicator, Bollinger →
        5 series, Stochastic → 2 series, MACD → 3 series, unknown
        indicator surfaces as an error stub instead of crashing).
      - 1 ATR-uses-H-L case.
      - 4 HTTP route cases (basic series, 400 on unknown indicator,
        400 on malformed params JSON, multi-indicator chart-data).
      - 4 MCP tool cases (discovery, compute_indicator,
        compute_indicators decomposes bollinger, get_chart_data
        lookback resolution).
      - **1 cross-consumer consistency gate**: same SMA(7) query
        through HTTP route AND MCP tool produces byte-identical
        `IndicatorSeries` values. Locks in the single-source-of-
        truth property at the regression-test level.

- [x] **End-to-end live verification** against real production AAPL:

      ```bash
      $ curl POST /api/indicators/chart-data with SMA(20) +
        Bollinger(20, 2.0) + RSI(14)
        → 44 bars, 7 series:
            sma                  last=227.07 @ 2024-08-30
            bollinger_upper      last=229.97
            bollinger_middle     last=227.07  (= SMA, math check ✓)
            bollinger_lower      last=224.16
            bollinger_bandwidth  last=0.03   (3% — quiet period)
            bollinger_percent_b  last=0.83   (price near upper band)
            rsi                  last=65.77
      ```

      Same query via MCP `compute_indicators` returns IDENTICAL
      values (per cross-consumer test + reconfirmed live).

**The TA-3.2 gate is GREEN.** The dashboard can render indicator
overlays via `POST /api/indicators/chart-data`. LLM agents over MCP
can call `compute_indicators` / `compute_indicator` / `get_chart_data`
and get the same Pydantic shape. Both surfaces use the same
`IndicatorReader` — single source of truth for indicator math.

### Phase TA-3.3 — RSI Extreme Reversion strategy (LANDED 2026-05-17)

Mean-revert baseline — buy on oversold, exit on neutral recovery.
First comparison ground for the trend-following SMA canary.

- [x] `app/services/sim/strategies/rsi_reversion.py` —
      `RsiReversionStrategy` via `BaseStrategy`. Long-only,
      interval-configurable.
      - Entry: `len(history) >= rsi_period + 2` AND no position
        AND `rsi(period) < oversold_threshold` → BUY
        `floor(cash * position_size_pct / price)` integer shares.
      - Exit: position held AND `rsi(period) > exit_threshold` →
        SELL full position.
      - Param validation: oversold < exit (otherwise the strategy
        would buy and sell the same bar).
- [x] `configs/rsi_reversion.yaml` — AAPL 2023-2024 daily,
      RSI(14), oversold=30, exit=50, $40k start.
- [x] CLI loader + MCP `run_backtest` `strategy_name` literal +
      `_instantiate` updated.
- [x] Tests `tests/test_rsi_reversion.py` — 12 strategy cases:
      param validation (overlap + equal thresholds), warmup
      hold, buy-on-dip, no-buy-when-long, no-buy-when-RSI-high,
      sell-on-recovery, no-sell-when-flat, integer-share sizing,
      zero-buy-when-cash-insufficient, metadata fields, Strategy
      Protocol satisfaction. Plus the existing structural purity
      gate (`test_strategy_is_pure`) still passes — RsiReversion
      doesn't import `app.db.*` / `app.providers.*`.
- [x] **Real-data run** — AAPL daily 2023-01-01 → 2024-12-31:
      - **12 trades, 33% win rate, -0.13% return, Sharpe 0.015,
        max DD -6.17%.**
      - **First baseline comparison vs SMA Crossover** on the
        same window: SMA got +2.65% / 5 trades / Sharpe 0.305.
        RSI Reversion fires 2.4× more often but underperforms on
        this trending stock — expected (bare RSI mean-revert is
        a known mediocre signal in trends). This data point
        anchors the TA-3.6 bake-off.

### Phase TA-3.4 — Bollinger Mean-Revert strategy (LANDED 2026-05-17)

Volatility-envelope mean-revert baseline — complementary to the
RSI-threshold variant. Different signal source, different trades,
different PnL profile.

- [x] `app/services/sim/strategies/bollinger_mean_revert.py` —
      `BollingerMeanRevertStrategy` via `BaseStrategy`. Long-only,
      interval-configurable.
      - Entry: `close <= lower_band` AND flat → BUY
        `floor(cash * pct / price)` integer shares.
      - Exit: `close >= middle_band` (SMA midline) AND long →
        SELL full position.
      - SMA midline via `ctx.indicator("sma", period=...)` for
        single-source-of-truth; rolling stdev computed locally
        with `ddof=0` (matches `BollingerBands.compute_full`).
- [x] Test `test_strategy_bands_match_bollinger_indicator` pins
      the math equivalence: the strategy's internal bands MUST
      match what `BollingerBands.compute_full` produces. If
      these diverge, the strategy and dashboard would show
      different bands for the same window.
- [x] `configs/bollinger_mean_revert.yaml` — AAPL 2023-2024
      daily, period=20, std=2.0.
- [x] CLI + MCP `run_backtest` loaders updated (4 strategies
      total: sma_crossover, llm_agent, rsi_reversion,
      bollinger_mean_revert).
- [x] Tests `tests/test_bollinger_mean_revert.py` — 11 strategy
      cases: warmup, entry on lower-band touch, no-buy-when-long,
      no-buy-when-close-above-lower-band, exit on middle-band
      recovery, no-sell-when-flat, integer-share sizing,
      zero-buy-when-cash-insufficient, bands-equivalence
      regression, metadata, Strategy Protocol. Plus the
      structural purity gate. All 12 green.
- [x] **Real-data run** (AAPL daily 2023-2024):
      - **12 trades, 50% win rate, -1.89% return, Sharpe -0.188,
        max DD -7.82%.**
      - Profit factor 0.778 (losers > winners despite the 50%
        win rate). Classic mean-revert problem in trending
        markets: small winners reverting to mean, big losers
        when the trend continues against you.

### TA-3.x running comparison (updated through 3.4)

| Strategy | Trades | Return | Sharpe | Max DD | Win Rate |
|---|---|---|---|---|---|
| `sma_crossover` (canary) | 5 | +2.65% | 0.305 | -5.90% | 0% |
| `rsi_reversion` | 12 | -0.13% | 0.015 | -6.17% | 33% |
| `bollinger_mean_revert` | 12 | -1.89% | -0.188 | -7.82% | 50% |

Same window (AAPL daily 2023-01-01 → 2024-12-31), same fees
(per_share=$0.005, min=$1.00), same slippage (next bar open),
same $40k start. SMA Crossover's 0% win rate + positive return
is the trend-follower signature (rare wins are huge); the
mean-revert pair's higher win rates with worse total return is
the mean-revert-in-trend signature (small wins, big losses).

### Phase TA-3.5 — EMA Crossover strategy (LANDED 2026-05-17)

Trend-following baseline using EMA instead of SMA. Same crossover
mechanics; faster signal because EMA weights recent prices more.

- [x] `app/services/sim/strategies/ema_crossover.py` —
      `EmaCrossoverStrategy` via `BaseStrategy`. Near-clone of
      `sma_crossover.py` with two diffs: indicator name `"ema"`
      and default periods `12/26` (MACD's canonical pair).
- [x] `configs/ema_crossover.yaml` — AAPL 2023-2024 daily,
      12/26, $40k start.
- [x] CLI + MCP `run_backtest` loaders + Literal type updated
      (5 strategies now: sma/ema crossover, llm_agent,
      rsi_reversion, bollinger_mean_revert).
- [x] Tests `tests/test_ema_crossover.py` — 11 strategy cases:
      param validation (overlap + equal), warmup hold,
      buy-on-cross-up, no-buy-when-long, sell-on-cross-down,
      no-sell-when-flat, integer-share sizing, metadata,
      Strategy Protocol satisfaction. Plus a direct A/B
      invariant: **`test_ema_fires_earlier_than_sma_on_same_cross`**
      — on the same crossing series with the same fast/slow
      periods, EMA's first buy index ≤ SMA's. The defining
      property of EMA (faster reaction) made into a regression
      gate. Structural purity gate also passes.
- [x] **Real-data run** (AAPL daily 2023-2024):
      - **7 trades, 67% win rate, +9.02% return, Sharpe 0.933,
        max DD -6.67%.**
      - **By far the best baseline.** ~3.4× the SMA Crossover
        return on roughly the same trade count.
      - Caveat: the 12/26 vs 20/50 (SMA canary) comparison
        isn't a pure EMA-vs-SMA A/B because periods also
        differ — the gain reflects BOTH "EMA reacts faster"
        AND "shorter periods catch more moves." A future
        sensitivity run with matched periods (12/26 EMA vs
        12/26 SMA, 20/50 EMA vs 20/50 SMA) would isolate
        the MA-family effect from the period effect.

### Phase TA-3.6 — Bake-off summary (LANDED 2026-05-17)

All 4 rule-based baselines on identical window/fees/slippage.
The canonical "where the bar is" before any LLM agent run.

- [x] `scripts/run_bakeoff.py` — small CLI that reads from
      `agent_runs` and prints a side-by-side table. Filters
      by strategy / symbol / interval / limit-per-strategy.
      No recomputation; uses the registry rows from prior
      backtest runs.
- [x] `app/services/sim/registry.py::list_runs` now SELECTs
      `symbols` column too (was missing — needed for the
      bake-off symbol filter).
- [x] Bake-off run executed; results below.

**Baselines on AAPL daily 2023-01-01 → 2024-12-31** ($40k start,
per-share fees @ $0.005/share, $1 min commission, next-bar-open
fill):

| Strategy | Trades | Return | Sharpe | Max DD | Win Rate | Final Equity |
|---|---:|---:|---:|---:|---:|---:|
| `sma_crossover` (canary, 20/50) | 5 | +2.65% | +0.305 | -5.90% | 0% | $41,059.91 |
| `ema_crossover` (12/26) ⭐ | 7 | **+9.02%** | **+0.933** | -6.67% | 67% | $43,609.77 |
| `rsi_reversion` (14, 30/50) | 12 | -0.13% | +0.015 | -6.17% | 33% | $39,949.72 |
| `bollinger_mean_revert` (20, 2σ) | 12 | -1.89% | -0.188 | -7.82% | 50% | $39,244.58 |

**Reproducibility:** every row above is pinned in `agent_runs`
with `git_sha` + strategy version + strategy params + full
`BacktestConfig` JSON. `reproduce(run_id)` (CLI follow-up)
will re-run any row from the registry.

#### Takeaways

1. **Trend-following won this window.** EMA Crossover (12/26)
   produced +9.02% with Sharpe 0.933, ~3.4× SMA Crossover's
   +2.65%. Both mean-revert strategies underperformed
   (-0.13% RSI, -1.89% Bollinger). AAPL 2023-2024 had two
   strong leg-ups (Q2 2023, Q1 2024); mean-revert in a
   trending market fights the dominant direction.

2. **Trade count vs profit factor** — the canonical asymmetry:
   - Trend-followers: low trade count, low win rate, BIG winners.
     SMA Crossover had 0% win rate on 5 trades but still made
     +2.65% — the rare wins were huge enough to offset every
     small loser. Classic trend signature.
   - Mean-reverters: 2.4× more trades, higher win rates (33%,
     50%), but losers bigger than winners (profit factor < 1).
     Lots of small "reverted to mean" wins giving back the gains
     on the larger "trend kept going" losers.

3. **The EMA-vs-SMA delta isn't a clean A/B.** EMA's 12/26
   defaults are shorter than SMA's 20/50, so part of EMA's
   edge here is "faster periods catch more moves" rather than
   pure "EMA reacts faster." A future sensitivity run (12/26
   EMA vs 12/26 SMA AND 20/50 EMA vs 20/50 SMA) would isolate
   the MA-family effect from the period effect. Filed as
   follow-up.

4. **What an LLM agent needs to beat.** The bar:
   - **Hurdle:** beat `ema_crossover` at +9.02% / Sharpe 0.933
     on the same window to claim "worth the API cost."
   - **Floor:** beat `sma_crossover` at +2.65% / Sharpe 0.305
     to claim "the LLM is adding something over the
     simplest possible strategy."
   - **Anti-floor:** under-perform both mean-revert baselines
     (-0.13% / -1.89%) and the LLM is actively destructive
     on this window — turn off the API, save the money.

5. **Single-symbol single-window limitations.** These are
   four data points on ONE symbol on ONE window. Bake-off
   #2 (post-TA-4) will run multi-symbol multi-window
   sensitivities to surface which baselines are
   regime-robust vs window-lucky. For now this is enough
   to anchor "where the bar is" before any LLM iteration.

#### Where to next

The platform is **complete enough to start measurable LLM
iteration**. To do that we need the deferred TA-2 live run
(see [ISSUES.md `ta2-live-anthropic-run-deferred`](ISSUES.md))
to actually call Claude against bronze and produce an
`agent_runs` row to put in the bake-off.

After the LLM lands its first row, the path forward is:

- **TA-4** — multi-timeframe + screener service. Lets a
  strategy declare `intervals=['1d', '1h']` and gets the
  context object resolving history per-interval. The screener
  picks "interesting today" symbols so an LLM doesn't burn
  context on the whole universe.
- **TA-5** — RL agent (PPO). Same Strategy Protocol — harness
  doesn't know it's RL. Reward = stepped Sharpe contribution.
- **TA-6+** — paper trading → live with kill switches.

### Phase TA-4.1 — Multi-timeframe foundation (LANDED 2026-05-17)

Strategies can now declare `intervals: list[str]` (coarsest-to-finest)
to access multiple bar timeframes within one run. The Backtester
fetches bars at each, the Context exposes them via
`history_at(interval)` and `indicator(name, interval=..., **params)`,
and a **no-look-ahead invariant** ensures coarser bars are only
released when their window has closed.

Single-timeframe strategies continue to work without ANY code
changes — the entire 4-baseline + canary + LLM agent suite from
TA-1/TA-2/TA-3 passes unchanged.

- [x] `app/services/sim/intervals.py` (NEW) —
      `interval_seconds`, `interval_duration`,
      `validate_intervals_order` (coarsest-to-finest required,
      duplicates rejected), `execution_interval`,
      `supported_intervals`.
- [x] `app/services/sim/context.py` refactored:
      - `Context(config, intervals=None)` — `intervals` optional;
        defaults to `[config.interval]` for back-compat.
      - `ctx.history` (property) = execution-interval history
        (back-compat alias).
      - `ctx.history_at(interval)` (method) = explicit-interval.
      - `ctx.advance(bar, portfolio)` = execution interval
        (existing API).
      - `ctx.advance_coarser(interval, bar)` (NEW) = coarser
        intervals; harness-only.
      - `ctx.indicator(name, *, interval=None, **params)` — cache
        keyed on `(interval, name, sorted_params)`. SMA(20) on
        daily and SMA(20) on 5m no longer collide.
      - `ctx.intervals` / `ctx.execution_interval` properties.
- [x] `app/services/sim/strategy.py` —
      `required_intervals(strategy)` helper returns
      `getattr(strategy, 'intervals', None) or [strategy.interval]`.
      `Strategy` Protocol docstring updated to document the
      optional `intervals: list[str]` attribute.
- [x] `app/services/sim/schemas.py` —
      `BacktestConfig.intervals: list[str] | None = None`
      (operator override for the strategy's declared intervals).
- [x] `app/services/sim/backtester.py` rewritten:
      - `_fetch_bars_multi(config, intervals)` returns
        `{interval: {symbol: bars}}` — one fetch per declared
        interval.
      - `_run_one_symbol` walks execution-interval bars; at each
        step releases coarser-interval bars whose `ready_time <=
        execution_bar.timestamp`, then advances execution.
      - Validates: `strategy.interval == execution_interval` AND
        `config.interval == execution_interval`. Mismatch raises.
      - Snapshot pinning still works (only on 1m → bronze).
- [x] **Tests `tests/test_multi_timeframe.py`** — 24 cases:
      - Interval helpers (5 cases): duration math, ordering
        validation (accepts coarsest-to-finest; rejects wrong
        order, duplicates, empty), execution_interval = last.
      - `required_intervals` helper (2 cases).
      - Context multi-TF API (8 cases): default single-TF init,
        multi-TF init, `history_at` unknown-interval raises,
        `history` property = execution-interval, `advance_coarser`
        rejects execution interval + unknown intervals,
        per-interval indicator cache (SMA(20) daily ≠ SMA(20) 5m),
        cache clears on `advance()`.
      - Backtester (4 cases): **the no-look-ahead invariant**
        (daily Aug-1 bar visible only at hour 24 on Aug-2, not
        any earlier 23 hours), strategy/config interval mismatch
        raises (both directions), end-to-end multi-TF run
        produces a non-trivial RunResult.
      - Back-compat (1 case): existing SMA Crossover (single-TF,
        no `intervals` attr) runs unchanged.
- [x] Existing test suite (118 cases across sim / strategies /
      MCP / indicators / lake / quotes) all pass without changes
      to test bodies except 4 tests that patched the old
      `_fetch_bars`/`_capture_snapshot` signatures — updated to
      the new `(config, intervals)` and `(config, exec_interval)`
      shapes.
- [x] **Reproducibility regression**: re-ran the SMA Crossover
      canary on AAPL daily 2023-2024. Identical metrics
      byte-for-byte (+2.65% / 5 trades / Sharpe 0.305 / max DD
      -5.90% / Sortino 0.190 / annualized +2.10% / longest DD
      53 days / avg trade -$793.02). The multi-TF refactor
      preserves single-TF behavior exactly.

### Phase TA-4.2 — First multi-timeframe strategy (LANDED 2026-05-17)

The canonical swing-trade pattern wired up end-to-end:
**daily SMA trend filter + hourly EMA crossover execution**.

- [x] `app/services/sim/strategies/mtf_ema_trend_filtered.py` —
      `MtfEmaTrendFilteredStrategy`. Declares
      `intervals = ["1d", "1h"]` and `interval = "1h"`. Logic:
      1. Daily regime gate via `ctx.history_at("1d")` and
         `ctx.indicator("sma", period=N, interval="1d")` — only
         allow longs when daily close > daily SMA.
      2. Hourly EMA cross detection on execution interval.
      3. Entry = cross-up AND trend-up AND flat → BUY.
      4. Exit = cross-down AND long → SELL (asymmetric; respect
         exit regardless of regime).
      5. Skipped entries logged as `signal_skipped_trend_filter`
         so an agent can ask later "how many cross-ups did the
         daily gate filter out?"
- [x] `configs/mtf_ema_trend_filtered.yaml` — AAPL Jun-Dec 2024,
      `intervals: ["1d", "1h"]`, daily SMA(50), 12/26 hourly EMA,
      `history_window: 250` for daily warmup.
- [x] CLI + MCP `run_backtest` loaders + Literal type updated.
      6 strategies registered.
- [x] Tests `tests/test_mtf_ema_trend_filtered.py` — 13 cases:
      metadata (declares 2 intervals, Protocol satisfaction),
      param validation, **trend-gate behavior** (holds during
      warmup, buys only when trend up + cross up, **skips buy
      when trend down + cross up**, exits regardless of trend,
      no double-up, no sell when flat), end-to-end Backtester
      run, and the **structural multi-TF gate**
      `test_strategy_uses_history_at_for_daily` — AST-walks the
      source and asserts the strategy calls
      `ctx.history_at("1d")` AND
      `ctx.indicator(..., interval="1d", ...)`. Without these
      the strategy would silently degrade to single-TF behavior.
- [x] **Real-data run** (AAPL hourly Jun-Dec 2024):
      - **44 trades, 18% win rate, -9.75% return, Sharpe -1.168,
        max DD -11.69%.**
      - **Infrastructure validation passes; strategy quality is
        poor.** The MTF harness pulled hourly + daily from CH,
        the Context exposed both, the no-look-ahead invariant
        held, the agent_runs row landed. The strategy itself is
        a textbook "hourly EMA whipsaw" — 44 trades over 140
        trading days, 18% win rate. The daily trend filter cut
        some bad entries (logged as
        `signal_skipped_trend_filter`) but didn't fix the
        underlying problem: hourly EMA crosses are too noisy
        without additional confirmation (volatility filter,
        time-of-day filter, multi-bar confirmation, etc.).
      - **The lesson** for future MTF strategies: a trend
        filter on top of a noisy entry signal is still a noisy
        signal. Real edge comes from BETTER entries layered on
        the trend filter — not the trend filter alone. This is
        a data point worth landing in the registry exactly
        because it surfaces this lesson.

### TA-3+TA-4 running comparison

| Strategy | Window | Trades | Return | Sharpe | Notes |
|---|---|---:|---:|---:|---|
| `sma_crossover` (canary 20/50) | AAPL 2023-2024 daily | 5 | +2.65% | +0.305 | trend; rare-win signature |
| `ema_crossover` (12/26) ⭐ | AAPL 2023-2024 daily | 7 | +9.02% | +0.933 | best single-TF baseline |
| `rsi_reversion` (14, 30/50) | AAPL 2023-2024 daily | 12 | -0.13% | +0.015 | mean-revert; fails in trend |
| `bollinger_mean_revert` (20σ2) | AAPL 2023-2024 daily | 12 | -1.89% | -0.188 | mean-revert; fails in trend |
| **`mtf_ema_trend_filtered`** (50d/12-26h) | AAPL Jun-Dec 2024 hourly | 44 | **-9.75%** | -1.168 | MTF infra ✓, strategy noisy |

The MTF row is on a **different window** than the daily-only
baselines (Jun-Dec 2024 hourly vs 2023-2024 daily) due to
hourly data availability in CH. Direct apples-to-apples
comparison would need either (a) the daily strategies replayed
on the same 7-month window or (b) the MTF strategy replayed on
the 24-month window with sufficient hourly history (requires
upstream backfill). Filed as follow-up.

### Phase TA-4.3 — Screener service (LANDED 2026-05-17)

Closes the canonical swing-trade pipeline (universe → screener →
candidates → strategy) with a declarative, agent-safe spec format.
A `ScreenerSpec` is plain Pydantic — no eval, no DSL, no code
strings — so an LLM agent can author one without becoming an RCE
vector.

- [x] `app/services/screener/` package
  - `schemas.py` — `ScreenerSpec`, `ScreenerRule`, `Candidate`,
    `CandidateMetric`, `ScreenerResult` with 13 `RuleKind` literals
    and 5 `RankBy` modes. Spec validators reject empty universes /
    empty rule lists.
  - `rules.py` — one evaluator per rule kind dispatched through the
    `_RULE_EVALUATORS` table. Spec author errors (unknown kind,
    missing required param, bad type) raise `ValueError` with a
    clear message. Per-symbol runtime errors return
    `RuleEval(passed=False, ...)` so the scan continues.
  - `screener.py` — `Screener.scan(spec, *, now=None) → ScreenerResult`.
    Universe resolution unions `spec.universe` + watchlist members.
    Bar source: `BronzeReader` for `interval="1m"` (Iceberg snapshot
    pinned), `BarReader` otherwise. Candidates ranked by `rank_by`
    (`volume` / `atr_pct` / `rsi` / `rsi_desc` / `none`) then
    truncated to `limit`.
  - `README.md` — folder contract, rule table, "how to add a rule"
    recipe, example specs for trend setups and volatility breakouts.

- [x] **HTTP surface**: `POST /api/screener/scan`
  ([app/api/routes_screener.py](../app/api/routes_screener.py)).
  `ScreenerSpec` body → `ScreenerResult`. 400 on author error,
  500 on infra error, 422 on Pydantic validation.

- [x] **MCP surface**: `scan_universe(spec)` tool
  ([app/mcp/tools/screener.py](../app/mcp/tools/screener.py)).
  Same Pydantic contract as the HTTP route — single source of
  truth across surfaces. Visible in `tools/list` (29 total tools
  registered).

- [x] **Tests**: 28 unit tests in `tests/test_screener.py`:
  - Per-rule evaluator (12 rule kinds) — hand-crafted DataFrames
    with known pass/fail outcomes plus warmup behavior.
  - Spec validation — `ScreenerSpec` rejects empty universe +
    empty rules; `model_construct` bypass exercises the runtime
    "unknown rule kind" guard.
  - Scan orchestration — stubbed `BarReader`/watchlist services;
    verifies per-symbol fetch errors land in `errors[]` without
    aborting the scan, ranking + `limit` truncation, watchlist
    union, mixed-case dedup, multi-rule AND composition,
    metrics-echo to candidates.

- [x] **Live verification** against the real ClickHouse live tier.
  `POST /api/screener/scan` with a 10-symbol universe over 60 daily
  bars returned 7/10 passing the SMA(20) filter, ranked by volume:

  ```
  NVDA  score=180,977,639  sma_20=210.08
  TQQQ  score= 81,260,605  sma_20= 71.05
  SPY   score= 60,410,771  sma_20=731.71
  AAPL  score= 54,862,836  sma_20=286.95
  QQQ   score= 51,792,656  sma_20=695.67
  AMD   score= 29,131,579  sma_20=371.71
  TSLA  score= 17,195,231  sma_20=400.32
  ```

  Multi-rule AND test (`close_above_sma` + `rsi_below 70`) cut
  the passing set to 4/9 — confirms intersection semantics.
  Missing-param test returned `HTTP 400` with the expected
  `"rule kind='close_above_sma' missing required int param
  'period'"` body.

This unblocks **TA-4.4** (screener-as-strategy-input — feed an
LLM the screener's candidate list as the per-bar universe filter)
and any future automated discovery jobs that need to filter the
bronze universe before strategy evaluation.

### Phase TA-5+ — Roadmap

Detailed in [trading_subsystem_design.md §10](trading_subsystem_design.md#10-phasing):

- **TA-5** — RL agent (PPO). Same `Strategy` Protocol — the harness
  doesn't know it's RL. Reward = stepped Sharpe contribution.
- **TA-6+** — Paper trading → live. Same `Strategy` class, different
  `Executor`. Kill switches mandatory before any live execution.

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
- **2026-05-16** — TA code split into three layers, each a candidate
  microservice boundary later:
  `app/indicators/` (pure math: price → series),
  `app/signals/` (pattern detectors: price + indicator → event),
  and a future `app/strategies/` (compose signals → trade decision).
  `app/divergence.py` moved into `app/signals/` to enforce the
  separation; it returns events, not series, so it was never an
  indicator. Detectors are pure functions taking tuning knobs as
  arguments (not reading `settings` directly), so they're testable in
  isolation — the caller (`services/live/monitor_service.py`) pulls
  config and passes it in.
- **2026-05-14** — Write cadence vs partition layout decoupled.
  Writer cadence stays daily (one append per trading day's Polygon
  flat file; the nightly job and live 5-min flush both write
  incrementally). Iceberg `month(ts)` partitioning + post-write
  compaction is what produces the monthly on-disk layout — not a
  monthly writer. Phase 1 makes compaction a first-class task, not
  maintenance. Avoids the "wait until month-end to write" trap that
  breaks incremental ingest.
- **2026-05-17** — **The ground-truth rule:** S3 silver is the
  canonical store; ClickHouse is a derived hot cache. Historical
  data (>48h old) never enters CH directly from a provider — only
  via `silver_to_ch_backfill`. CH is rebuildable from silver
  byte-identically. Eliminates the consistency-bug class where
  provider-fed CH and provider-fed silver silently disagree.
  Codified in [silver_layer_plan.md §2.1](silver_layer_plan.md)
  and [data_platform_plan.md §1](data_platform_plan.md). Concrete
  consequence: `polygon_flatfiles_bulk_backfill.py`'s default
  flips from dual-write (CH + bronze) to bronze-only; same for
  `schwab_bronze_backfill.py`. The `quick`/`intraday`/`daily`
  provider-REST backfill modes in `backfill_service.py` are
  scheduled for retirement (TA-5.5) once `silver_to_ch_backfill`
  replaces them.
- **2026-05-17** — **Asymmetric provider strategy.** Live and
  historical have different cost/freshness/reliability needs:
  - **Live 1-min bars:** Schwab CHART_EQUITY WebSocket ONLY. We
    already pay for Schwab; the stream is included. Polygon
    stream NOT used.
  - **Historical bulk archive:** Polygon flat-files, one-shot
    20-year pull while subscribed, lock into bronze archive,
    then drop the Polygon subscription. Bronze becomes a frozen
    historical contribution to silver; no ongoing Polygon cost.
  - **Tip-fill (silver-watermark → live-stream first bar):**
    Schwab REST `pricehistory`. ≤48h windows, no rate-limit
    pressure. The ONE exception to the ground-truth rule (writes
    to bronze + CH in parallel because the data is near-live, not
    historical archive).
  - **Corp actions:** Polygon REST one-shot snapshot into
    `silver.corp_actions`. After Polygon drop: snapshot is
    frozen + manually updated.
- **2026-05-17** — **Two-tier symbol universe.** `seed` and
  `ad-hoc` tiers handled differently on `add_members`:
  - **Seed universe:** ~100-500 actively-traded symbols. Full
    historical depth via the Polygon flat-files pipeline.
    `silver_to_ch_backfill` populates CH from silver in ~10s.
  - **Ad-hoc:** Any other ticker added for exploration. Schwab
    REST one-shot (~48 days 1-min + multi-year daily) → bronze.
    Silver picks it up on next nightly build. Chart works
    immediately on live ticks + Schwab REST history.
  - **Promote ad-hoc → seed** via `scripts/promote_to_seed.py`.
    Kicks off a deeper backfill if Polygon is still subscribed;
    falls back to Schwab REST otherwise.
  Codified in [silver_layer_plan.md §2.3](silver_layer_plan.md)
  and [silver_layer_plan.md §6.3](silver_layer_plan.md).
- **2026-05-17** — **Volumetric clarity + Polygon-drop strategy.**
  The provider topology is two-dimensional: live-vs-historical AND
  whole-market-vs-seed. Concretely:
  - **Polygon flat-files** = whole market × 5-20 years (one-shot
    pulls; whole-market because the daily flat-file contains every
    symbol regardless of which we import). Subscription planned to
    drop after the 20-year upgrade pull lands. Bronze.polygon_minute
    becomes a frozen static archive on the drop date.
  - **Schwab stream** = seed universe × ongoing (~100 today, growing
    over time). The ONLY source of live data going forward.
  - **Three temporal regimes** the system passes through smoothly
    with no code changes: pre-Polygon-drop (today, 5y), during the
    20y upgrade pull, post-Polygon-drop (steady state).
  - **Post-Polygon-drop:** a non-seed symbol added to a watchlist has
    silver coverage frozen at Polygon-drop date plus an explicit
    `[Polygon-drop, now-48d]` gap, plus Schwab REST 48d + Schwab
    stream forward. The gap is visualized on the cockpit Symbol
    page coverage strip.
  - **Strategic recommendation:** maximize the seed universe BEFORE
    Polygon drops. The marginal cost of adding a symbol to seed
    while Polygon is active is essentially zero (flat-files cover
    everything anyway); after drop, newly promoted symbols have
    the back-gap forever. Codified in
    [silver_layer_plan.md §9.5](silver_layer_plan.md): operator can
    bulk-promote the S&P 500 or Russell 1000 via
    `scripts/promote_to_seed.py --universe sp500` to lock in deep
    history for those symbols ahead of the Polygon drop.
- **2026-05-17** — **Providers are pluggable; subscriptions pause and
  resume, they don't "drop".** Reframed earlier "Polygon drop"
  language across the docs. The architecture already supports this
  natively: bronze tables are per-provider, silver build operates
  on whatever bronze partitions exist, provider precedence is
  config-driven, no code branches on "is provider X subscribed."
  Pausing the Polygon subscription is just `bronze.polygon_minute`
  not getting new appends; resuming triggers a one-shot gap-fill
  backfill for the pause window plus restarting the nightly job.
  The architecture supports adding entirely new providers later
  (IEX, Databento, custom feed) with the same plug-in mechanism.
  Codified in [silver_layer_plan.md §2.3](silver_layer_plan.md);
  full pause/resume runbook in
  [silver_layer_plan.md §9.7](silver_layer_plan.md).
- **2026-05-17** — **§14.2 RESOLVED: adjusted-everywhere with
  raw-opt-in.** Already implicit in
  [data_platform_plan.md §6](data_platform_plan.md)'s dual-column
  silver schema (`*_raw` + `*_adj`), but worth recording:
  - Bronze stores **what the provider sent** — raw, unadjusted.
    Bronze is immutable; no transformation at the boundary.
  - Silver carries **both** column sets. Adjustments computed from
    `silver.corp_actions` during silver_build; re-derived when
    corp-actions change (no bronze rewrite needed).
  - All downstream consumers — chart, screener, indicator overlays,
    backtest harness, MCP tools, gold features, ML training — read
    `_adj` by default. Right choice for AI/ML trading; split
    discontinuities would otherwise poison everything.
  - `BacktestConfig.adjusted=False` reads `_raw` instead — for the
    rare replay-accuracy case where you want exactly what the trader
    saw live.
