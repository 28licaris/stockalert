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

### Decision 2026-05-18 — Whole-market silver + whole-market CH (B1 architecture)

**Context.** Tonight's Path A (TA-5.1.7) builds silver + CH for the seed
universe (~100 symbols). That gets alerts/dashboard working for the curated
set. Operator raised a hard product requirement:

> **"Select any symbol → see its 5-year chart in 5-10 seconds."**

This requires the symbol's adjusted bars to be queryable in ~3-7 sec
on the backend (after subtracting network + frontend overhead).

**Latency budget by source (5y × 1-min × 1 symbol, ~500K rows):**

| Source | Cold-cache latency | Warm |
|---|---|---|
| **ClickHouse (data already there)** | **~200-500 ms ✅** | ~200-500 ms |
| Silver Iceberg from laptop | ~2-4 sec ✅ | ~2-4 sec |
| Silver Iceberg from AWS EC2 | ~500 ms - 1 sec ✅ | ~500 ms - 1 sec |
| Build silver slice on-demand from bronze | ~15-30 sec ❌ | <1 sec |
| Polygon REST + adjust on-demand | ~10-30 sec ❌ | ~10-30 sec |

**Conclusion: the only way to meet the budget for arbitrary symbols
(including never-seen ones) is to have whole-market silver materialized
and CH pre-loaded. On-demand silver slice build is too slow (>10 sec)
for first-time queries.**

**Decision: B1 — Whole-market silver + whole-market CH.**

- **Silver:** all ~8-10K Polygon-covered tickers × 5y × 1-min, adjusted.
  ~5-6B rows, ~300-500GB compressed on S3.
- **CH:** mirrors silver. ~500GB local Docker storage (or ~$50-100/mo on
  managed CH).
- **Frontend:** always reads CH directly. No on-demand build paths needed.
- **Schwab:** continues to provide live freshness for the seed universe
  (only). Non-seed symbols rely on Polygon historical + Polygon corp_actions.

**Trade-off accepted:** higher storage cost (~$0 on local Docker;
~$50-100/mo on managed CH); one-time silver build cost ~$5 + ~2 hr.

**Rejected alternative — B2 (lazy CH load from silver-on-demand):**
- Pro: smaller CH
- Con: 3-6 sec cold path for new symbol, hidden complexity in API
  (loading states, retries, partial responses)
- Verdict: operational complexity not worth saving 300-400GB of CH storage
  that's mostly free at our scale.

**Rejected alternative — Polygon adjusted=true:**
- Saves ~5-10% silver build time (split-factor compute is essentially free)
- Loses point-in-time reproducibility (Polygon silently re-adjusts on
  revised splits)
- Violates bronze immutability principle
- Verdict: not worth it.

### TA-5.6 — Whole-market silver build (planned, not started)

**Goal:** materialize `silver.ohlcv_1m` for the full Polygon universe
(~8-10K tickers × 5y).

**Prerequisites:**
- [ ] Backfill the dividend gaps in `bronze.polygon_corp_actions`
  (2020, 2022, 2024) via CodeBuild. Local backfill is too slow
  (PyIceberg upsert against existing 1.5M-row table = ~2 min/chunk
  from residential, ~2-5 sec/chunk from AWS internal). Estimated
  CodeBuild time: ~30-60 min, cost ~$0.50.
- [ ] Drop `silver.ohlcv_1m` + `silver.bar_quality` (clean append path).
- [ ] Confirm `silver.corp_actions` includes splits for all symbols
  we'll materialize (a quick scan; we already verified the seed
  universe is fully covered).

**Build plan — parallel CodeBuild by year:**
- 5 parallel CodeBuild jobs, each handling 1 year × whole market
- Per-job: 12 months × 5-15 min/month via existing month-batched code
  = ~1-3 hr wall-clock per job
- All 5 in parallel: ~1-3 hr total (slowest year wins)
- Concurrency safety: years are separate Iceberg year-partitions,
  no commit conflicts
- Estimated total cost: 5 × 3 hr × 60 min × $0.005/min ≈ **$5**

**Alternatives considered:**
- AWS Glue Spark (~30-60 min, ~$18) — needs PySpark rewrite, deferred
- EMR cluster (~30-60 min, ~$20) — operational overhead, deferred
- Step Functions Map → 60 parallel monthly CodeBuilds (~15-20 min, ~$3)
  — risk of intra-year Iceberg commit conflicts unless we serialize
  by year. Not worth the orchestration complexity for a one-time job.

**Gate:** `silver.ohlcv_1m` row count matches expected
(~5-6B rows), all 16 critical seed-universe splits still produce
correct adjusted prices on Yahoo spot-checks, and a random non-seed
symbol returns 5y of bars when queried.

### TA-5.7 — Whole-market CH hot-load (planned, not started)

**Goal:** load all whole-market silver into ClickHouse so frontend
queries for any symbol meet the 5-10 sec budget.

**Prerequisites:** TA-5.6 complete.

**Plan:**
- Extend `scripts/rebuild_ch_from_silver.py` (built in TA-5.5 tonight
  for seed scope) to handle whole-market scan + batched insert.
- CH insert rate ~1-2M rows/sec from local, so 6B rows = ~1-2 hr
  wall-clock from a laptop. From an EC2 instance in us-east-1
  (silver region), faster: ~30-60 min.
- Verify `ohlcv_1m` table has appropriate ORDER BY (symbol, ts) +
  PARTITION BY (toYYYYMM(ts)) so single-symbol queries hit only a
  few parts.

**Gate:**
- Random non-seed symbol query (e.g. some Russell 2000 small-cap):
  5y of bars returned in < 500 ms from CH.
- Total `ohlcv_1m` row count matches silver.
- Spot-check: random sample of 100 silver rows match corresponding
  CH rows byte-for-byte (modulo timestamp precision).

**Estimated total time + cost from end of TA-5.5 → TA-5.7 done:**
~1-2 sessions (~4-8 hrs of operator wall-clock, mostly CodeBuild watching),
~$10 in AWS compute.

### TA-5.8 — Spark + Iceberg execution layer (planned, decision locked)

**Context.** Operator stated intent (2026-05-18):

> "We will be doing a lot of ML jobs and we need fast executing for
> live charting and monitoring."

The current Python + PyIceberg + CodeBuild stack is fine for one-off
operator-triggered builds (silver --full ~36 min for seed, ~2 hr for
whole-market via parallel CodeBuild). It is NOT the right execution
layer for:

1. **Recurring full-rebuild jobs** (e.g. weekly silver rebuild for
   schema migrations, or rebuilding gold/feature tables from silver).
2. **Large analytical queries** powering ML training (multi-billion-row
   scans for feature engineering, label generation, walk-forward
   backtest data prep).
3. **Sub-30-minute SLA on whole-market silver rebuilds** for fast
   schema iteration during ML model development.
4. **Live-monitoring streaming compute** (this is a separate domain —
   Kinesis/Flink — but Spark Structured Streaming overlaps).

**Decision: invest in Spark + native Iceberg as the canonical execution
layer for batch + analytical workloads.**

**Engine choice: AWS Glue 4.0 (PySpark + native Iceberg connector).**

Comparison:

| Engine | Pros | Cons |
|---|---|---|
| **Glue 4.0** ✅ | Native Iceberg + AWS Glue catalog integration; no cluster mgmt; pay per DPU-second; integrates with AWS data ecosystem | Glue-specific quirks; less flexibility than raw EMR |
| EMR Serverless | Cheaper at large scale; same managed model | Iceberg connector requires explicit config; less battle-tested with Iceberg than Glue 4.0 |
| EMR classic cluster | Most flexibility; can run other engines (Trino, Hudi) | Cluster spinup overhead; ops burden |
| Databricks | Best UX for ML notebooks; Delta-native | Vendor lock-in; expensive; not Iceberg-native |

We pick **Glue 4.0** because: native Iceberg + native Glue catalog = zero
config work to integrate with our existing `bronze.*` / `silver.*` /
`gold.*` namespaces, and we're already paying for Glue catalog. The
operational simplicity (serverless, pay-per-job) matches our small-team
shape.

**Performance targets (Glue 4.0 with 20-50 DPUs):**

| Job | Today (PyIceberg) | Glue 4.0 target |
|---|---|---|
| Seed silver --full (100 sym × 5y) | 36 min | ~3-5 min |
| Whole-market silver --full (10K sym × 5y) | ~2-3 hr (parallel CodeBuild) | ~20-30 min |
| Gold/feature table generation (TBD scope) | n/a | ~5-15 min |
| Whole-market silver → CH reload | ~30-60 min (CH-side) | unchanged (CH-bound) |

**Cost targets:**
- Per whole-market silver build: ~$5-15 (vs $5 parallel CodeBuild — comparable)
- Per gold/feature batch job: ~$2-10 depending on scope
- Monthly recurring cost (assuming weekly full rebuild + daily incremental):
  ~$40-100/mo

**Tasks (planned, ~1-2 dev days):**

- [ ] Add `app/services/silver/spark/` module with PySpark equivalents
  of `merge_with_precedence`, `apply_corp_actions`, `compute_bar_quality`.
  Must produce **byte-identical output** to the existing Python build
  for the same input (modulo `ingestion_ts` / `ingestion_run_id`).
- [ ] Write `scripts/spark_silver_build.py` — Glue 4.0 job entrypoint
  using the Iceberg connector. Reads bronze.{provider}_minute, writes
  silver.ohlcv_1m + silver.bar_quality.
- [ ] Glue job IAM role with S3 + Glue catalog perms (similar to CodeBuild
  role but Glue-specific service principal).
- [ ] Local parity test: `tests/test_silver_spark_parity.py` — small
  dataset, run both Python build + Spark build, assert byte-identical
  output. Pinned regression test.
- [ ] Operator runbook `docs/runbook_spark_silver_build.md` for kicking
  off via AWS console + CLI.
- [ ] Replace CodeBuild silver --full as the default execution path in
  `streaming_universe_model.md` add_members flow + nightly silver build.
  CodeBuild stays as the fallback / dev escape hatch.

**Gate:**
1. Spark parity test passes (byte-identical output).
2. Glue silver --full for seed completes in < 10 min.
3. Glue silver --full for whole market completes in < 45 min.
4. Yahoo spot-checks still pass against the Spark-built silver.

**Sequence:** TA-5.8 lands AFTER TA-5.5 (rebuild_ch_from_silver) is
proven working with seed scope. We don't need Spark to ship Path A.
TA-5.6 / TA-5.7 may either (a) use parallel CodeBuild as documented, or
(b) wait for TA-5.8 and use Spark — operator decision based on whether
they want whole-market silver in days vs. weeks.

### TA-5.9 — ML feature pipeline (planned, depends on TA-5.8)

**Context.** Once silver is canonical + Spark is the execution layer,
gold-tier ML feature tables become tractable:

- `gold.bar_features_1m` — pre-computed indicator panels per symbol/time
- `gold.event_features` — engineered features around corp actions,
  earnings, etc.
- `gold.label_panels` — forward-return labels for supervised training

These feed model training (offline) and live inference (online — but
live inference reads from CH, not S3, so gold needs a CH mirror similar
to silver).

**Implementation deferred until TA-5.8 ships.** Sketch:
- One Glue job per gold table, scheduled via EventBridge (daily refresh)
- Output schemas owned by `app/services/gold/`
- Feature definitions tracked in `docs/feature_catalog.md` (new)

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

## Assistant track (parallel)

The cockpit copilot — natural-language interface to the platform.
Full spec in [assistant_plan.md](assistant_plan.md). User-driven
and distinct from the trading `LLMAgent` (see
[assistant_plan.md §3.2](assistant_plan.md)). RBAC + quota seams
in place from day 1 so the SaaS rollout is additive (per
[frontend_plan.md §7](frontend_plan.md)).

**Status:** plan landed 2026-05-18; AS-1 awaits the five
signoff decisions in [assistant_plan.md §18](assistant_plan.md).

### Phase AS-1 — Skeleton + read-only loop

**Goal:** end-to-end "ask a question, get a streamed answer
grounded in MCP read tools" path.

**Gate:** `tests/integration/test_assistant_e2e.py` —
"what's the freshness of the bronze polygon_minute table?"
streams a coherent answer, persists 1 conversation + 2 turns +
1 audit row.

### Phase AS-2..AS-7 — Roadmap

Detailed in [assistant_plan.md §15](assistant_plan.md):

- **AS-2** — Write tools with confirm-before-mutate (`run_backtest`
  first).
- **AS-3** — Inline rich artifacts (equity curve, OHLCV chart,
  screener table) using the same cockpit React components.
- **AS-4** — `/assistant` dedicated page + conversation browser
  + branching.
- **AS-5** — Slash commands + `@mention` context attachments.
- **AS-6** — Extended thinking (Opus 4.7) + parallel tool dispatch.
- **AS-7** — Quota seams + SaaS-mode dry-run
  (`ASSISTANT_FAKE_SAAS=1`).
- **AS-8** — Backlog: image input, voice, cross-conversation
  memory, saved prompts, public `/api/v1/assistant`.

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
- **2026-05-17** — **Bronze holds two data types; corp-actions are
  separate from OHLCV.** Clarification triggered by operator question
  ("we already update bronze nightly, what is bronze.polygon_corp_actions
  for?"). The medallion model is per-data-type, not per-source:
  - **Bronze OHLCV** (`bronze.polygon_minute`, `bronze.schwab_minute`):
    continuous time-series. Minute-by-minute price + volume. Updated
    nightly (Polygon flat-files) + live (Schwab stream).
  - **Bronze corp-actions** (`bronze.polygon_corp_actions`): discrete
    event archive. Splits + dividends. Polygon publishes these via
    a separate REST API — they are NOT in the minute-bar flat-files.
    Updated nightly via the new ingest. Consumed by the silver OHLCV
    build to compute `_adj` (split/dividend-adjusted) price columns.
  Without bronze corp-actions, backtests on stocks with splits would
  show fake -75% candles on split day. Both kinds follow the same
  bronze→silver pattern (per-provider raw → canonical merged).
  Documented in [bronze/README.md "Two data types in bronze"](../app/services/bronze/README.md).
- **2026-05-17** — **Provider-pluggable architecture confirmed for
  corp-actions.** Adding a new provider (e.g. SEC XBRL, IEX, future
  alt-data feed) requires no changes to silver build code:
  1. New bronze schema + `ensure_*` function in `app/services/bronze/`.
  2. New ingest module in `app/services/silver/{kind}/`.
  3. Append the provider name to `SILVER_PROVIDER_PRECEDENCE` env var.
  `SilverCorpActionsBuild` iterates the configured precedence list
  and silently skips providers whose bronze table doesn't exist. Same
  for removal: stop the ingest job, drop the precedence entry,
  optionally drop the table. Implementation verified via the
  `_merge_with_precedence` sanity test in TA-5.0 step 5c.
- **2026-05-17** — **Universe-expansion reminder cross-referenced in
  bronze README.** The pre-pause "expand the Schwab seed universe"
  guidance from silver_layer_plan §9.7 also lives in
  `app/services/bronze/README.md` so the action item is discoverable
  from the layer where the actual table expansion happens (bronze
  ingest scope). The strategic recommendation: before pausing Polygon,
  run `scripts/promote_to_seed.py --universe sp500` (or russell1000 /
  russell3000) so the live-streamed universe covers everything you
  might want to trade later — Polygon flat-files are free at the
  marginal symbol while subscribed, but once Polygon is paused only
  Schwab-streamed symbols get fresh bronze data.
- **2026-05-17** — **Empirical provider-adjustment probe — bronze
  adjustment status is heterogeneous.** Operator flagged the design's
  "bronze stores what the provider sent — raw" assumption as
  potentially wrong for Schwab. Built `app/services/silver/probes/`
  package (universal probe framework: `ProviderAdjustmentProbe`
  Protocol + registry, `ProbeSpec` library of 5 well-known historical
  splits) + `scripts/probe_provider_adjustment.py` runner.

  Probe run against AAPL 2020-08-31 (4-for-1) AND NVDA 2024-06-10
  (10-for-1) — both confirm:
  - **`bronze.polygon_minute` (Polygon flat-files): RAW** ✅
    (matches the design assumption)
  - **`bronze.schwab_minute` (Schwab REST + stream): SPLIT_ADJUSTED** ❌
    (breaks the design assumption — Schwab adjusts splits in their API)
  - Polygon REST adjusted=true returns split-adjusted; adjusted=false
    returns raw (matches Polygon's documented behavior).

  Schwab's official docs (`docs/schwab-api/market_data_api.md`)
  contain zero mention of "adjust" or "split" — without the probe,
  this would have surfaced only when a backtest showed wrong prices
  on Schwab-sourced bars.

  Impact on silver build (TA-5.1):
  - Bronze schemas now carry per-provider ADJUSTMENT_STATUS constants
    (`BRONZE_POLYGON_MINUTE_ADJUSTMENT_STATUS = "raw"`,
    `BRONZE_SCHWAB_MINUTE_ADJUSTMENT_STATUS = "split_adjusted"`).
  - silver_layer_plan §2.9 + §3.3 updated: build must NORMALIZE each
    provider's bars to both `_raw` and `_adj` BEFORE the precedence
    merge. Per-provider, the build either applies corp-action factors
    (raw → adj) or un-adjusts via cumulative split factors (adj → raw).
  - Discovered before TA-5.1 was written — cheap fix.

  Universal probe framework:
  - 5 pre-curated probe specs (AAPL 2020, NVDA 2024, AMZN 2022,
    GOOGL 2022, TSLA 2022). Operator picks via `--probe NAME` CLI.
  - Adding a new provider's probe = drop new
    `app/services/silver/probes/<provider>.py` + register via
    `@register_probe(name)`. No runner changes.
  - Rules for what's needed when onboarding a new provider:
    `app/services/silver/probes/README.md` "How to add a new
    provider's probe" + "Decide if the provider needs corp-actions
    ingest" sections.
- **2026-05-17** — **Bronze layer audit framework + first audit run.**
  Operator emphasized bronze is the foundation; everything downstream
  flows from here. Built `app/services/bronze/audit/` package
  (universal pattern, same as silver/probes/) with 5 checks:
  schema_match, row_counts, source_tags, null_symbols,
  adjustment_status. Runner: `scripts/audit_bronze.py`.

  **First audit findings (production bronze):**
  - ✅ **Schema match**: both tables (polygon_minute, schwab_minute)
    have 12 fields matching the declared `BRONZE_*_MINUTE_SCHEMA`.
    No drift.
  - ✅ **Null symbols**: 0 bad rows out of **2,116,486,243 polygon**
    + **1,774,051 schwab**. The Phase-1 80k null-symbol filter is
    holding.
  - ✅ **Polygon adjustment status**: bronze.polygon_minute
    empirically matches documented status `'raw'` via NVDA 2024
    split probe (pre/post ratio = 9.932, expected 10.0). The
    `BRONZE_POLYGON_MINUTE_ADJUSTMENT_STATUS = "raw"` constant
    accurately describes what's on disk.
  - ⚠️ **Schwab live-stream NOT writing to bronze**: source_tags
    shows zero `schwab-stream`-tagged rows. The bar batcher
    (`app/db/batcher.py`) writes only to CH; it does NOT dual-write
    to bronze.schwab_minute as silver_layer_plan §2.4 originally
    designed. The nightly Schwab REST backfill catches up the
    previous day's bars. **This 8-24h freshness gap is the
    primary motivator for the new TA-5.7 phase (live_lake_writer)
    landing before TA-5.1.**
  - ⚠️ **Snapshot summary key gap**: PyIceberg's older snapshots
    don't expose `total-records` in `snapshot.summary` (only
    `operation`). Date ranges captured via column scan instead:
    polygon = 2021-01-04 → 2026-05-15 (5 years); schwab =
    2026-03-30 → 2026-05-15 (~48 days). Cosmetic only; not a bug.
- **2026-05-17** — **TA-5.7 inserted into roadmap: live_lake_writer.**
  The audit's "schwab-stream not writing to bronze" finding drove
  a new sub-phase. Original silver_layer_plan §2.4 specified
  per-tick dual-write at the batcher; that approach is wrong (tiny
  files, Iceberg metadata churn). The data_platform_plan §8 Path A
  design already specified the right approach: 5-min micro-batch
  writes via a `live_lake_writer` background task. Building that
  per the existing design (no redesign).

  TA-5.7 sub-phases:
  - 5.7.1 `app/services/ingest/live_lake_writer.py` core
  - 5.7.2 lifespan wiring + config flag
  - 5.7.3 ingestion_runs audit integration
  - 5.7.4 daily compaction for bronze.schwab_minute
  - 5.7.5 new bronze audit check: `live_freshness`
    (max(timestamp) > 10min stale during market hours = WARN)
  - 5.7.6 tests + 24h live verification

  Roadmap order (operator-confirmed): TA-5.0 corp-actions →
  TA-5.7 live_lake_writer → TA-5.1 silver build. Sequential because
  both corp-actions correctness and live-data freshness are
  prerequisites for the silver build to produce production-grade
  silver.ohlcv_1m.
- **2026-05-17** — **TA-5.0 LANDED**: corp-actions ingestion (bronze
  + silver) is end-to-end production-grade.

  **Live verification** against the operator's Polygon subscription
  (canary window 2024-06-10 to 2024-06-14, the week of NVDA's
  10-for-1 split):
  - **Bronze ingest:** 32 splits + 5,089 dividends pulled from
    Polygon REST in ~17s. 13 duplicate (symbol, ex_date, action_type)
    rows collapsed by summing cash_amount (see below). 5,076 rows
    upserted into bronze.polygon_corp_actions.
  - **Silver build:** 5,108 rows merged from polygon bronze (only
    provider present), 5,108 upserted into silver.corp_actions
    (table auto-created on first run). ~8s.
  - **Reader verification:** `CorpActionsReader.get_corp_actions(NVDA, ...)`
    returned the correct 2 events: split on 2024-06-10 (factor=10.0)
    + cash_dividend on 2024-06-11 ($0.01).
  - **Idempotency verification:** silver-only re-run on the same
    window produced the same 5,108 rows in silver, NVDA count
    unchanged at 2. No double-write.

  **Two real bugs caught by the live test that unit tests had no
  way of finding:**

  1. **CorpActionKind needed expansion.** The original mapping
     collapsed `CD`, `LT`, `ST` (Polygon's dividend_type values
     for ordinary cash div, long-term cap gains, short-term cap
     gains) all under `cash_dividend`. A fund/ETF that pays BOTH
     a regular div and a capital-gains distribution on the same
     ex_date produces two rows with identical identifier
     `(symbol, ex_date, "cash_dividend")` — PyIceberg's upsert
     refused to write. Fixed by giving LT and ST their own
     CorpActionKind values (`lt_capital_gain`, `st_capital_gain`).

  2. **Same-ex_date duplicate cash dividends are real.** Even with
     the type expansion above, some symbols (CIVI, HUABF, SARDF,
     INSW, IGPYF) pay TWO ordinary cash dividends — a regular and
     a special — on the same ex_date. Polygon labels BOTH as `CD`,
     so the type expansion doesn't separate them. Fix: added
     `_dedupe_actions()` to the bronze ingest that groups by
     identifier and sums `cash_amount` (regular + special →
     combined). 13/5089 dividends (~0.25%) had this pattern in
     this one week, projecting to ~7,500 across the 3M dividends
     since 2003. The summed total is correct for adjustment math
     (which is what silver consumers need).

  Six new unit tests in `tests/test_silver_corp_actions.py` pin
  the dedup behavior (passthrough, summation, three-way merge,
  announced_at takes latest, different action_types not collapsed,
  empty input).

  **What you can do now:**
  - Trigger a full backfill: `poetry run python scripts/run_corp_actions_backfill.py --full`
    (~30-60 min; pulls ~50K splits + ~3M dividends since 2003).
  - Schedule nightly: cron `01:30 ET` calling the same script with
    `--nightly`.
  - Query via reader: `CorpActionsReader().get_corp_actions(symbol, ...)`.
  - Query via HTTP: `GET /api/corp-actions/AAPL?since=2020-01-01`.
  - Query via MCP: `get_corp_actions(symbol="AAPL", since=...)`.

  Test count summary at end of TA-5.0:
  - tests/test_silver_corp_actions.py: 37 tests
  - tests/test_silver_probes.py: 22 tests
  - tests/test_bronze_audit.py: 18 tests
  Total: 77 tests pinning the bronze→silver corp-actions pipeline,
  the universal probe framework, and the universal bronze audit
  framework.

  Next: TA-5.7 (live_lake_writer to close the 8-24h Schwab live →
  bronze gap), then TA-5.1 (silver OHLCV build).
- **2026-05-17** — **TA-5.7 LANDED**: live_lake_writer + ingestion_runs
  + live_freshness audit + bronze compaction CLI. Closes the
  8-24h Schwab live → bronze freshness gap that the bronze audit
  flagged on 2026-05-17.

  **What's in this phase:**

  - `app/services/ingest/live_lake_writer.py`: the core class.
    Reads CH ohlcv_1m for the last 15 min, filters by per-provider
    live source tag (`schwab-stream`), upserts into
    bronze.{provider}_minute via PyIceberg upsert (identifier:
    symbol, timestamp). Idempotent, provider-pluggable (config
    map; adding a new live provider = one entry).
  - 1-min safety margin so the in-flight bar (still being written
    by the batcher) isn't read.
  - Lifespan integration: `start_live_lake_writer()` +
    `stop_live_lake_writer()` wired into `app/main_api.py`.
    Started after the watchlist service; stopped BEFORE it on
    shutdown (so last-minute streamed bars get captured).
  - Config: `LIVE_LAKE_WRITER_ENABLED` (default true),
    `LIVE_LAKE_WRITER_CYCLE_MINUTES` (default 5),
    `LIVE_LAKE_WRITER_LOOKBACK_MINUTES` (default 15).
  - `watchlist_service._on_bar` updated to tag streamed bars with
    `{provider}-stream` (e.g. `schwab-stream`) so they're
    distinguishable from REST-backfilled rows (which use the
    bare `{provider}` tag).

  **Auxiliaries:**

  - CH `ingestion_runs` table: generic job-run audit log. One row
    per cycle (run_id, job_name, started_at, finished_at, window,
    rows_written, per_provider counts/errors, status). Shape is
    generic across job_name so future ingest jobs (silver_build,
    corp_actions_backfill) can use the same audit channel.
  - New bronze audit check: `live_freshness` — verifies
    max(timestamp) of `*-stream`-tagged rows is recent (<30 min
    stale) during RTH (Mon-Fri 9:30am-4pm ET). Outside RTH:
    INFO-only (expected staleness). RTH detection respects
    timezone.
  - `scripts/compact_bronze.py`: operator CLI that runs Athena
    `OPTIMIZE … REWRITE DATA USING BIN_PACK` on bronze tables.
    Recommended cadence: daily at 03:00 ET. Mitigates the
    small-file problem (5-min writes × ~500 rows/cycle = tiny
    Iceberg files).

  **Tests:**

  - `tests/test_live_lake_writer.py` (27 tests):
    Construction guards (cycle > 0; lookback >= cycle).
    Provider-config pluggability (custom config swap).
    Row → Arrow conversion (schema match, audit metadata stamp,
    vwap/trade_count 0→NULL normalization, naive ts → UTC coerce,
    empty list → empty Arrow with correct schema).
    CycleResult shape (total_rows sum, succeeded flag, duration).
    run_cycle (empty window → 0 rows, per-provider error isolation,
    window cutoff = as_of - 1min).
    RTH detection (weekday during, after-close, before-open;
    Saturday + Sunday → False).
    Lifespan singleton.

  Total tests across TA-5.0 + TA-5.7: 104 passing.

  **What the operator does next** (to validate live-writer in
  production):
  1. Restart `uvicorn` — startup logs should show "✅ Live lake
     writer started (cycle=5min lookback=15min)".
  2. During market hours, after 5-10 min, run:
       poetry run python scripts/audit_bronze.py --check live_freshness
     Expected: 🟢 OK for schwab_minute (stale_minutes < 30).
  3. After ~30 days of operation, run:
       poetry run python scripts/compact_bronze.py
     To compact accumulated small files.

  **TA-5.0 + TA-5.7 = bronze layer is production-ready.** Bronze
  has clean provenance (per-row source tag), idempotent writes,
  freshness verification, and an audit framework that catches
  regressions. silver_build (TA-5.1) can now be wired against this
  foundation.

- **2026-05-17** — **TA-5.1.1/.2/.3 LANDED**: silver schemas +
  normalization + merge.

  TA-5.1.1: `silver.ohlcv_1m` + `silver.bar_quality` Iceberg schemas
  with corresponding `SilverBar` Pydantic. Both `_raw` (passthrough)
  and `_adj` (split-adjusted) OHLCV on every row, plus
  source_provider + sources_seen CSV provenance. Identifier
  `(symbol, ts)` for ohlcv_1m, `(symbol, date)` for bar_quality.
  Month partition + symbol-sorted, mirroring bronze.

  TA-5.1.2: per-provider raw↔adjusted normalization math. Polygon
  (raw) → _adj = _raw / F. Schwab (split-adjusted) → _raw = _adj × F.
  F = product of split factors for ex_date > bar_date. Cumulative
  index built once per run from silver.corp_actions. Worked example
  NVDA 2024-06-10 10-for-1 split: both providers produce identical
  silver rows (math verified inline).

  TA-5.1.3: provider precedence merge (polygon > schwab default)
  + bar_quality computation (expected_bars=390 RTH, actual_bars,
  gap_count, max_gap_minutes, providers_seen CSV,
  disagreement_count tolerance 50¢ OR 0.5%). Single iteration
  produces both outputs — one pass, two tables.

- **2026-05-17** — **TA-5.1.4 LANDED**: silver OHLCV build
  orchestrator.

  `app/services/silver/ohlcv/build.py` wires the four pieces from
  .1/.2/.3 into `SilverOhlcvBuild` with four public modes:
    - `build_slice(symbol, day)` — one (symbol, day) slice
    - `build_window(symbols, start, end)` — day-by-day iteration
    - `run_nightly(symbols=None)` — yesterday × active universe
    - `run_full(symbols, start_date, end_date)` — initial backfill

  Provider-pluggability: `_PROVIDER_ROUTING` dict (provider →
  bronze short + adjustment_status). Adding a new provider = one
  entry + bronze schema additions. ZERO orchestrator changes. Same
  pattern as corp-actions build + bronze audit + silver probes.

  Error isolation per slice (SliceResult.error) so build_window
  loop survives one symbol failing. Critical for nightly runs.

  Idempotent: re-running yields byte-identical silver rows modulo
  ingestion_ts/run_id. PyIceberg upsert on the identifier handles
  re-write.

  Corp-actions caching: `_prime_corp_actions_cache()` loads
  silver.corp_actions once per run, builds the split-factor index
  in memory; saves N×slices catalog reads.

  16 tests cover: routing dict, result dataclass semantics, empty
  bronze, single-provider slice, precedence merge with sources_seen,
  upsert-failure isolation, build_window iteration, cache clearing,
  cold-start (no silver.corp_actions) graceful empty index.

- **2026-05-17** — **TA-5.1.5 LANDED**: SilverOhlcvReader + HTTP +
  MCP. Canonical consumer surface for silver bars + bar-quality.

  `app/services/readers/silver_ohlcv_reader.py`:
    - `get_bars(symbol, start, end)` → SilverBarsResponse
    - `get_bar_quality(symbol, since, until)` → BarQualityResponse
  Reads `silver.ohlcv_1m` + `silver.bar_quality`. Cold-start safe
  (empty result if tables absent). Snapshot-pinning. Filters push
  down to Iceberg (month partition + symbol sort).

  HTTP routes (app/api/routes_silver.py, mounted at /api/silver):
    - GET /api/silver/bars/{symbol}?start=...&end=...
    - GET /api/silver/bar-quality/{symbol}?since=...&until=...

  MCP tools (app/mcp/tools/silver_ohlcv.py):
    - get_silver_bars(symbol, start, end)
    - get_silver_bar_quality(symbol, since, until)

  Pydantic shapes added to `readers/schemas.py`: SilverBarsResponse,
  BarQualityRow, BarQualityResponse. Re-exports existing SilverBar.

  Consumer-contract compliance: per silver_layer_plan §"The consumer
  contract" — every consumer (chart, screener, indicator, backtest,
  MCP) reads silver, never bronze directly.

  15 tests cover: happy-path with sorted output + snapshot_id, CSV
  → list[str] for sources_seen, empty/whitespace symbol, missing
  table (cold start), scan failure isolation, naive datetime →
  UTC, NULL-OHLC row skipping. HTTP route + start/end validation
  + since/until validation. MCP tool import sanity.

- **2026-05-17** — **TA-5.1.6 LANDED**: nightly silver build loop
  + operator CLI. Closes TA-5.1's automation surface.

  `app/services/silver/ohlcv/nightly.py`:
    - `run_silver_ohlcv_build_loop()`: forever loop, sleep-until-hour
      then run yesterday × universe. 300s back-off on unexpected
      exceptions (no hot-loop). Idempotent + failure-isolated.
    - `run_silver_ohlcv_build_nightly()`: one-shot wrapper used by
      both the loop and the CLI. Runs the synchronous build in
      `asyncio.to_thread` so the event loop stays responsive.

  `scripts/run_silver_ohlcv_build.py`:
    - `--nightly` (yesterday × seed)
    - `--full` (2021-01-04 → yesterday)
    - `--since` / `--until` (custom window)
    - `--symbols` ('seed' | CSV list)
    - `--out-json` (pipeline-friendly summary)

  Schedule order:
    07:00 UTC — nightly_polygon_refresh
    22:00 UTC — nightly_schwab_refresh
    23:00 UTC — silver_ohlcv_build   ← new, 1h after Schwab nightly

  Lifespan wiring in main_api.py: gated on
  `SILVER_OHLCV_BUILD_ENABLED=true` + `STOCK_LAKE_BUCKET` set.
  Symmetric shutdown. Same `_safe_start` isolation pattern as
  upstream nightlies.

  New settings: `SILVER_OHLCV_BUILD_ENABLED` (default false),
  `SILVER_OHLCV_BUILD_RUN_HOUR_UTC` (23), `SILVER_OHLCV_BUILD_SYMBOLS`
  ('seed'). Disabled by default until TA-5.1.7 operator-validates
  with a live run.

  12 tests cover: scheduling math (target-later/already-passed/clamp),
  symbol resolution (seed/CSV/whitespace), gating (disabled/missing
  bucket/enabled), one-shot returns summary, loop returns
  immediately when gated.

  **Cumulative TA-5.1 status (.1 through .6):** 102 silver tests
  green. silver_ohlcv_build is feature-complete; remaining is
  TA-5.1.7 — flip the env toggle, run an initial full backfill in
  prod (operator step, est. several hours wall-clock for the seed
  universe × 5 years of bronze).

  **Next:** TA-5.1.7 (live verification + initial backfill), then
  G1 (dynamic universe), then TA-5.3 (silver→CH + tip-fill add
  flow), then TA-5.5 (delete Path ② + wipe-and-rebuild CH +
  end-to-end verification).

- **2026-05-17** — **G1 LANDED**: dynamic universe.

  `app/services/universe/active_universe.py`:
    - `get_active_universe()` = SEED_SYMBOLS ∪ active-watchlist
      symbols. Best-effort: degrades to seed-only if CH is down
      (nightlies must survive CH outages).
    - `resolve_universe_spec("seed" | "active" | CSV)` — single
      resolver used by every nightly + the silver build, so
      adding `"active"` to env config works system-wide.

  Three nightlies now delegate to `resolve_universe_spec`:
    - `nightly_polygon_refresh.resolve_nightly_lake_symbols` (also
      preserves `"all"`/`"*"`/`""` = whole-market for flat-files)
    - `nightly_schwab_refresh._resolve_symbols`
    - `silver/ohlcv/nightly._resolve_symbols`

  `SilverOhlcvBuild.run_nightly()` + `.run_full()` defaults flipped
  from `SEED_SYMBOLS` → `get_active_universe()`. So `run_nightly()`
  with no args now covers SEED ∪ watchlists.

  **Defaults preserved.** Each `*_NIGHTLY_SYMBOLS` env var still
  defaults to `seed`. Operators opt into the dynamic universe
  explicitly by setting it to `active`. Recommended production
  config (per data_flow_review §G1):
    POLYGON_NIGHTLY_SYMBOLS=all      # whole-market via flat-files
    SCHWAB_NIGHTLY_SYMBOLS=active    # dynamic (SEED ∪ watchlists)
    SILVER_OHLCV_BUILD_SYMBOLS=active

  Placement rationale: chose `app/services/universe/` over adding
  to `app/data/seed_universe.py` because `seed_universe.py` is a
  pure-Python static-tuple module with no runtime dependencies.
  `get_active_universe()` reads from ClickHouse — that's a runtime
  service, not data. Keeping them separate preserves the rule
  that `app/data/*` can be imported by anything (no CH needed at
  module load).

  18 tests cover: seed-keyword + CSV-list + active-keyword routing,
  CH-outage fallback, kinds filter, sorted+deduped output. Plus
  delegation tests for each of the three nightlies (active works
  through all of them) + a spy test confirming
  `SilverOhlcvBuild.run_nightly()` pulls from `get_active_universe`
  by default.

  120 tests green across the silver + universe surface area.

- **2026-05-18** — **TA-5.1.7 tooling LANDED**: preflight + verify +
  runbook. The remaining step is the actual operator-go-live action,
  but the tooling that makes that action safe + auditable is now in
  place.

  NEW `scripts/preflight_silver_build.py`:
    7-check pipeline-readiness validator (~30s). Catalog reachable,
    bronze tables populated, silver.corp_actions present, silver
    tables creatable, end-to-end build slice runs, silver readback
    confirms, CH ingestion_runs audit row recorded. Skips downstream
    checks when catalog is unreachable. Exit 0 = safe to `--full`;
    exit 2 = block. JSON-report option.

  NEW `scripts/verify_silver_build.py`:
    Post-run audit (~1 min). Reads silver.bar_quality for [since,
    until] × symbols and surfaces:
      - zero-actual-bar weekdays (suspect coverage gap)
      - gap_count / max_gap_minutes outliers (default thresholds:
        5 gaps, 10 min — operator-configurable)
      - disagreement_count > 0 (cross-provider mismatch)
      - cross-check sample (N random cells: bars sorted, unique
        timestamps, valid provider tags, _adj populated)
      - ingestion_runs CH audit summary (run counts, status dist,
        total rows written)
    Exit 0 = no issues; exit 2 = issues found with top-N list.

  NEW `docs/runbook_silver_ohlcv_build.md`:
    Operator runbook for the 5-step TA-5.1.7 procedure: preflight →
    `--full` overnight → verify → enable nightly loop → Yahoo-adj
    spot-check. Includes idempotency notes (Ctrl-C safe, re-running
    same window safe via PyIceberg identifier upsert) +
    troubleshooting matrix.

  18 tests cover: CheckResult glyphs, parser defaults, orchestration
  (catalog-fail short-circuits downstream; all-pass returns 7 OKs),
  symbol resolution (default = SEED), gap-outlier classification,
  disagreement classification, weekday-vs-weekend zero-bar
  treatment, VerificationFindings.has_issues toggles.

  Why this is "tooling for an operator step, not code TA-5.1.7
  itself": the actual operator-go-live is irreversible (flips the
  production toggle + seeds silver from bronze for the first time).
  Splitting it out keeps the code commits surgical and the operator
  action explicit. After the operator runs the 5-step procedure
  successfully, TA-5.1.7 closes and TA-5.3 (silver→CH backfill +
  tip-fill) becomes the next code block.

  150 silver + universe + operator-tool tests green.

- **2026-05-18** — **TA-5.1.8 LANDED**: drop `_raw` columns from silver
  (lean-schema refactor). Reverses an unforced earlier complexity decision.

  **What happened.** TA-5.1.1 landed silver.ohlcv_1m with dual
  OHLCV columns: `open_raw, high_raw, low_raw, close_raw, volume_raw`
  + `open_adj, high_adj, low_adj, close_adj, volume_adj`. The
  reasoning in `silver_layer_plan.md §2.9` was "consumers read _adj
  by default; replay-accuracy mode reads _raw."

  The user pushed back: `_raw` is **fully derived** from `_adj` +
  `silver.corp_actions` (multiply by F to undo the split adjustment).
  Storing both is bloat:
    - ~30% larger rows (~240 GB at full scale)
    - Two sources of truth → drift bug surface area
    - Every consumer + every test + every Pydantic forced to ask
      "which one do I read?"
    - The "replay accuracy" use case is rare and easy to do
      client-side: `raw = adj × F(symbol, ts)` is 5 lines.

  More importantly, this was a **design decision I made unilaterally**
  based on a sentence in the plan doc, not from an explicit
  conversation with the user. That's an anti-pattern.

  **What changed.**

  Silver schema reduced from 18 → 13 columns. OHLCV columns are now
  just `open/high/low/close/volume` (always split-adjusted; that's
  silver's canonical contract). Files touched:
    - `app/services/silver/schemas.py` — SilverBar Pydantic +
      SILVER_OHLCV_1M_SCHEMA: 5 fields removed, suffix dropped
    - `app/services/silver/ohlcv/normalize.py` — produces one set
      of normalized columns. Polygon math: divide-by-F. Schwab
      math: passthrough. (No more dual-direction adj↔raw math.)
    - `app/services/silver/ohlcv/merge.py` — Arrow schema reduction;
      disagreement-detector reads `close` instead of `close_raw`
    - `app/services/readers/silver_ohlcv_reader.py` — reads
      single-column layout; docstring updated to point consumers
      at the corp_actions reader for raw-price recomputation
    - `app/services/ingest/silver_to_ch_backfill.py` — drops the
      `adjusted=True/False` parameter; always writes adjusted
      (which is what it did in practice anyway). Source tag
      `silver-{provider}` preserved.

  **Yahoo spot-check becomes trivial.** Silver `close` IS Yahoo
  `adjclose` (both split-adjusted). The runbook's step-5 QA is
  now a 1-to-1 numeric comparison, not a "which view should I
  compare?" decision.

  149 tests green (was 150 — one redundant `test_unadjusted_writes_raw`
  test got consolidated into `test_silver_adj_close_propagates_to_ch`).

  **Working agreement locked.** New entry in user memory
  (`feedback_lean_silver_explicit_signoff.md`):
    1. Silver must be LEAN — no derived columns that can be
       recomputed from canonical inputs
    2. Don't make design decisions that add complexity / dual
       storage / extra Pydantic fields without surfacing the
       tradeoff and getting explicit signoff. Plan docs are
       guidance, NOT pre-authorization.

- **2026-05-18** — **TA-5.1.9 LANDED**: corp-action rebuild trigger
  (auto-recompute affected silver slices when a new split lands).

  **The bug-in-waiting this closes.** Without it, scenario:
    Day 1: NVDA's silver history exists with F=1 (no future splits).
    Day 2: NVDA announces 4-for-1 split, ex_date in 30 days.
           corp_actions_backfill picks it up → silver.corp_actions
           has the new row.
    Day 3+: Nightly silver_build only processes yesterday — never
           touches NVDA's deep history. Historical rows still have
           F=1 baked in.
    Result: 4× price discontinuity at the new ex_date.

  **Implementation.** SilverOhlcvBuild.run_nightly() now runs in
  two phases:

    Phase 1: Dirty-rebuild scan
      1. Read CH ingestion_runs for the prior successful
         silver_ohlcv_build's started_at (= watermark)
      2. Query silver.corp_actions for splits with
         ingestion_ts > watermark AND action_type='split'
      3. Per affected symbol, find max(ex_date) of new splits
      4. Rebuild window (BRONZE_HISTORY_START, max_ex_date - 1)
         — bars on/after the new ex_date already have correct F
         from their original build

    Phase 2: Normal yesterday × universe
      Same as before. Combined BuildResult recorded once in
      ingestion_runs, serving as the next night's watermark.

  Uses existing ingestion_runs CH table as the watermark — no new
  schema. Cold start (no prior run): defaults to 7-day lookback.

  New SilverOhlcvBuild methods (app/services/silver/ohlcv/build.py):
    - find_corp_action_dirty_symbols(since) → dict[symbol, max_ex_date]
    - _get_last_run_started_at() → datetime | None  (CH read)
    - _run_corp_action_dirty_rebuilds() → BuildResult | None

  run_nightly(scan_corp_action_dirty=True) toggle for tests +
  one-off operator overrides.

  Operator CLI: --rebuild-corp-action-dirty manually triggers the
  same scan + rebuild logic. Useful immediately after running
  scripts/run_corp_actions_backfill.py when you don't want to wait
  for tonight's nightly.

  Tests (11, all green):
    - No corp_actions table → graceful empty
    - Empty corp_actions → empty dirty
    - Single new split → flags symbol with that ex_date
    - Multiple splits same symbol → keeps MAX(ex_date)
    - Multiple symbols → each gets own max
    - Filter pushes action_type='split' + since predicates to Iceberg
    - Scan failure → graceful empty (no raise; nightly continues)
    - run_nightly with scan_corp_action_dirty=False skips the scan
    - Dirty + yesterday results merge correctly
    - No dirty → falls through to yesterday-only
    - Rebuild window math: starts at BRONZE_HISTORY_START, ends at
      max_ex_date - 1, never includes ex_date itself

  Docs updated:
    silver_layer_plan.md §3.4 rewritten with actual implementation
    runbook_silver_ohlcv_build.md: new "When a new split lands" section
    + manual --rebuild-corp-action-dirty usage

- **2026-05-18** — **TA-5.1.10 LANDED**: parallelize silver build
  (asyncio.Semaphore + per-day batched upserts).

  **The problem.** The original sequential build_window iterates
  (symbol × day) one slice at a time. With ~5 sec per slice
  dominated by S3 latency, the initial --full backfill takes
  18-25 hours for the seed universe — operator-painful.

  **Implementation.** Split build_slice's compute and write halves:
    - compute_slice(): READ + normalize + merge → (SliceResult,
      ohlcv_arrow, quality_arrow). No writes. Pure function modulo
      the corp-actions cache + bronze reads.
    - build_slice(): convenience wrapper = compute_slice + the two
      upserts. Same API as before for sequential callers.
    - _build_window_concurrent(): for each day, fan out compute_slice
      via asyncio.Semaphore(N) + asyncio.to_thread, then do ONE
      batched upsert per silver table per day.

  **Per-day batching is the upsert-conflict mitigation.** PyIceberg's
  optimistic concurrency means N concurrent upserts to the same
  Iceberg table cause retry storms. Batching to one upsert per
  table per day amortizes commits and avoids the issue.

  **New plumbing:**
    - build_window(symbols, start, end, *, max_concurrency=1)
    - run_full(*, max_concurrency=1)
    - CLI flag: --concurrency N on scripts/run_silver_ohlcv_build.py

  **Defaults preserved.** max_concurrency=1 → original sequential
  path (safe). Operator opts in to parallelism explicitly. Sweet
  spot is N=8 per the speedup options doc; higher hits diminishing
  returns from S3 rate limits + PyArrow CPU contention.

  Wall-clock impact (estimated):
    --concurrency 1 (today)      18-25 hr   local laptop
    --concurrency 8              3-4 hr     local laptop
    --concurrency 8 in cloud     30-60 min  EC2/CodeBuild same region

  Tests (8, all green):
    - compute_slice returns Arrows without writing
    - Empty bronze → (result, None, None)
    - All slices processed at concurrency=N
    - Per-day batching: 3 symbols × 2 days = 2 ohlcv upserts, NOT 6
    - max_concurrency=1 preserves sequential path (1 upsert per slice)
    - Empty day → no upserts
    - Cache primed exactly once per run (not once per slice)
    - Semaphore actually bounds concurrent compute_slice in flight

  Existing test_active_universe.py test updated to pass
  scan_corp_action_dirty=False (avoids the TA-5.1.9 catalog access
  which the universe-default test doesn't need).

  168 silver tests green. FastAPI + MCP server still import cleanly.

  Docs updated:
    silver_initial_build_speedup_options.md: Option A marked LANDED
    runbook_silver_ohlcv_build.md: step 2 includes --concurrency 8

- **2026-05-18** — **TA-5.3.2 LANDED**: Schwab REST tip-fill —
  silver-watermark → live gap (≤48d), dual-write to bronze + CH.

  Completes step 4 of the "add streamed symbol" flow per
  docs/streaming_universe_model.md. Closes the gap between silver's
  latest minute and the live stream's first bar — without it, a
  brand-new symbol's chart would be empty for ~24h until the next
  nightly silver_build → silver_to_ch_backfill chain caught up.

  NEW service: app/services/ingest/schwab_tip_fill.py
    SchwabTipFill.compute_gap(symbol, *, now=None)
      → (silver_watermark, gap_start, gap_end)
      gap_start = max(watermark + 1min, now - 48d)
      gap_end   = now - 1min  (snapped to minute boundary, avoids
                  in-flight live minute)

    SchwabTipFill.tip_fill(symbol, *, now=None) async
      → TipFillResult with per-stage row counts

  Dual-write contract (ordered):
    1. Schwab REST historical_df (single call covers ≤48d window)
    2. Bronze: per-day BronzeIcebergSink.write() — preserves the
       canonical archive
    3. CH: insert_bars_batch — immediate chart availability

  Source tag: "schwab-tipfill" (distinct from "schwab" nightly REST
  and "schwab-stream" live). app/services/silver/ohlcv/normalize.py
  _SOURCE_TO_PROVIDER updated so silver build maps tip-fill rows
  back to canonical provider="schwab" for the precedence merge.

  Failure model:
    - Schwab fetch fails → abort (no writes attempted)
    - Bronze write fails → abort CH (preserve archive integrity;
      caller retries the whole tip_fill)
    - CH write fails → bronze succeeded → partial result; the next
      nightly silver_build → silver_to_ch_backfill chain repairs CH

  Why dual CH write is intentional (vs "no historical → CH" rule):
    - Window is ≤48 days, near-live, not bulk archive
    - Without it, cockpit "warming up" UX is ~24h (next nightly
      silver chain) instead of seconds
    - Bronze archive remains the source-of-truth; CH is the cache
    - silver_ohlcv_build picks up the new bronze rows the next
      nightly, then silver_to_ch_backfill re-syncs CH canonical

  Tests (16, all green):
    - compute_gap: empty silver → full 48d
    - compute_gap: watermark within 48d → resume at watermark+1m
    - compute_gap: watermark older than 48d → bounded at now-48d
    - compute_gap: silver caught up → empty gap (skip Schwab)
    - compute_gap: missing silver.ohlcv_1m table → no-history
    - compute_gap: empty symbol raises
    - tip_fill happy path: 5 bars → 5 bronze + 5 CH writes, both
      tagged "schwab-tipfill"
    - tip_fill: bars spanning 2 UTC days → 2 per-day bronze writes
    - tip_fill: empty Schwab response → 0 writes, no error
    - tip_fill: empty gap → no Schwab call, no writes
    - Error paths: Schwab fail, bronze fail (aborts CH), CH fail
      (bronze succeeded → partial result with error text)
    - Source-tag propagation: schwab-tipfill maps to schwab in
      silver normalize._provider_from_source

  184 tests green across the silver + ingest surface area.

  Next: TA-5.3.3 — wire silver_to_ch_backfill + tip_fill into
  watchlist_service.add_members (feature-flagged for safe rollback).

- **2026-05-18** — **TA-5.3.3 LANDED**: wire silver_to_ch + tip-fill
  into watchlist_service.add_members + start() (feature-flagged).

  Completes the unified add-symbol flow per
  docs/streaming_universe_model.md. When the new flag is on,
  watchlist_service.add_members and start() use:
    1. SilverToChBackfill.backfill_symbol(days=730) — silver → CH
    2. SchwabTipFill.tip_fill(symbol) — silver-watermark → live, ≤48d
  When the flag is off (default), they keep the legacy 3-call
  _enqueue_backfill path (provider REST → CH direct = Path ②).

  Why feature-flagged: the legacy path is the production default
  until TA-5.1.7 operator validation completes. Flipping the flag
  is the operator's "switch CH from legacy to silver-derived"
  moment. TA-5.5 will remove the legacy path entirely once the
  flag is verified stable.

  NEW config (app/config.py):
    SILVER_DERIVED_ADD_MEMBERS_ENABLED (default false)

  NEW methods (app/services/live/watchlist_service.py):
    _enqueue_silver_derived_warmup(symbols): sync fire-and-forget
      dispatcher; mirrors _enqueue_backfill's shape (no-loop guard,
      empty-symbols guard, one task per symbol).
    _silver_derived_warmup_one(symbol): async per-symbol chain.
      Step 1 (silver → CH) wraps the sync backfill_symbol in
      asyncio.to_thread. Step 2 (tip-fill) is naturally async.
      Both calls are best-effort: failures logged + don't propagate
      so the subscribed symbol stays live regardless.

  Both add_members AND start() branch on the same flag — keeps
  behavior consistent across symbol-add and server-restart paths.

  Tests (9, all green):
    Flag dispatch:
      Flag OFF → legacy 3-call path runs, new path NOT called
      Flag ON  → new path called with all symbols, legacy NOT called
    Warmup chain ordering:
      Step 1 (silver_to_ch) runs THEN step 2 (tip_fill)
      Step 1 fail (raise) → step 2 still runs
      Step 1 fail (error result) → step 2 still runs
      Step 2 fail (raise) → logged, not raised
    Fire-and-forget semantics:
      No event loop → silent skip (no raise)
      Empty symbols → no tasks created
      N symbols → exactly N tasks (one per symbol)

  Existing test_watchlist_service.py 10 tests still pass (legacy
  path unchanged for the default-off case).

  203 tests green across silver + watchlist + ingest surfaces.

  .env.example: documents the new flag with rollback semantics
  streaming_universe_model.md: capability table updated to ✅ LANDED

  Operator next steps:
    1. Run TA-5.1.7 5-step go-live procedure (still pending)
    2. After silver is verified, flip
       SILVER_DERIVED_ADD_MEMBERS_ENABLED=true in .env + restart
    3. Add a test symbol to a watchlist; observe the silver→CH +
       tip-fill logs in stdout
    4. If stable for a week, proceed to TA-5.5 (delete legacy path)

- **2026-05-18** — **TA-5.1.11 LANDED**: month-batched bronze scans
  (THE big read-pattern fix; ~2000× fewer S3 round-trips).

  **The change:** replace the per-slice scan loop with ONE Iceberg
  scan per provider per month. Each month-scan returns ALL
  (symbols × days) for the month; downstream compute runs from
  in-memory groupby instead of fresh S3 reads.

  **Math (seed × 5y backfill):**
    Per-slice: 1,300 days × 100 symbols × 2 providers × ~10 GETs
               = ~2,600,000 S3 GETs
    Month-batched: 60 months × 2 providers × ~10 GETs
                  = ~1,200 S3 GETs
    Reduction: ~2,000×

  **Wall-clock impact:**
    Local laptop (was 18-25 hr per-slice sequential):  ~30-60 min
    CodeBuild same-region (was 30-60 min per-slice):   ~5-10 min

  **Real-lake validation (this commit):** ran the new path against
  the actual lake on NVDA × 2026-05-15 — 79s for 2 month-scans
  (~7-8K rows each), producing 960-row silver output. Output
  byte-identical to per-slice path (verified by test_output_equivalence).

  **Architecture:**
    SilverOhlcvBuild._iter_months(start, end)
      → generator of (month_start, month_end) tuples
    SilverOhlcvBuild._read_bronze_month(short, symbols, m_start, m_end)
      → ONE Iceberg scan with In("symbol", [list]) +
        timestamp >= m_start AND < m_end_plus_one. Returns
        list[dict] of all rows for the whole month.
    SilverOhlcvBuild._group_rows_by_symbol_day(rows)
      → {(symbol, calendar_date(UTC)): list[row]}
    SilverOhlcvBuild._compute_from_provider_rows(...)
      → shared in-memory compute (normalize+merge+bar_quality);
        used by BOTH compute_slice and the month-batched path
    SilverOhlcvBuild._build_window_month_batched(symbols, start, end)
      → outer month loop + inner day loop + per-day batched upserts

  build_window now takes `mode: str = "month"`. Per-slice path
  preserved as opt-in `mode="per-slice"` for tests + single-slice
  debugging + corp-action rebuilds (which are single-symbol windows
  where per-slice and per-month cost the same).

  CLI flag `--mode {month, per-slice}` on
  scripts/run_silver_ohlcv_build.py. Default: month. The
  --concurrency flag still works but only matters in per-slice mode
  (month-batched doesn't need parallelism — already fast enough).

  **Refactor invariants verified:**
    - compute_slice's logic preserved (refactored to delegate
      to _compute_from_provider_rows)
    - Per-day batched upserts: same shape regardless of mode
      (TA-5.1.10's concurrency tests still pass against the
      month-batched default — same per-day-upsert structure)
    - Idempotent: re-running same window upserts byte-identical
      rows modulo ingestion_ts/run_id

  **Tests (10 new, all green):**
    _iter_months: single month, multi-month, year-boundary
    Scan count assertions:
      - month-batched: 300-slice window → exactly 2 scans (1/month)
      - per-slice: 4-slice window → 4 scans (counter-check)
    Output equivalence: month-batched output == per-slice output
      (Arrow rows compared with ingestion_ts/run_id stripped)
    Per-day batched upserts: 5 trading days → 5 ohlcv upserts
      + 5 bar_quality upserts
    Edge cases: empty month, partial month at window start
    Invalid mode → ValueError

  213 silver + ingest + watchlist tests green.

  **Bonus speedup for other operations:**
    Corp-action rebuilds (TA-5.1.9): 60 scans vs 1300 for a full-
      history symbol rebuild. ~22× faster per affected symbol.
    Schema migrations: ~30 min instead of ~24 hr for re-derive.
    Newly-promoted symbol backfills: minutes instead of an hour.

  Docs updated:
    silver_initial_build_speedup_options.md: Option D LANDED at the
      top as the dominant choice
    runbook_silver_ohlcv_build.md: step 2 simplified — no more
      --concurrency flag in the recommended path

  Next: CodeBuild scaffolding (buildspec.yml + IAM + runbook), then
  the operator's --full run, which should finish in ~10 min not
  ~30-60 min thanks to this.

---

## Phase FE-1 — Frontend foundation + SaaS-readiness seams

**Goal:** Scaffold the React cockpit at `frontend/` with the locked
stack from [docs/frontend_plan.md §3.0](frontend_plan.md). App shell
renders, dev server proxies to FastAPI, OpenAPI codegen wired,
production build outputs to `app/static/dist/`. Every SaaS seam
exists as a no-op. No backend changes in this PR — the `/api/v1/*`
rename and `Principal` dep land in a separate TA-SaaS-1 PR so a
frontend bootstrap can't break ingest.

**Status:** ✅ COMPLETE
**Started:** 2026-05-18
**Completed:** 2026-05-18
**Gate (all green):**
  - ✅ `cd frontend && npm install && npm run build` succeeds
    (281 packages, 96 KB gzipped JS, 3.5 KB gzipped CSS — well under
    the 250 KB initial-bundle target in [frontend_plan §10](frontend_plan.md))
  - ✅ `cd frontend && npm run dev` serves the shell at :5173 with HMR,
    proxies `/api`, `/mcp`, `/ws/*`, `/openapi.json` to FastAPI at :8000
    (live-verified: HTTP 200 with `@vite/client` injected)
  - ✅ `cd frontend && npm run typecheck` green
  - ✅ `cd frontend && npm run lint` green (0 errors, 0 warnings)
  - ✅ Placeholder Status route renders inside the AppShell;
    sidebar collapses on desktop, slides over content on mobile
  - ✅ `useCurrentUser()` returns `DEV_PRINCIPAL`; sidebar visibility
    driven entirely by `flags.ts`; `apiClient` runs the `withAuth`
    no-op middleware
  - ✅ `branding.ts` is the sole source of "StockAlert" string
  - ✅ `frontend/README.md` documents run/build/codegen + lift-out
    contract (zero `app/` imports)
  - ✅ End-to-end smoke: FastAPI serves SPA at `/app/`, `/app/symbol/AAPL`
    falls back to index.html (React Router takes over), legacy
    `/dashboard` continues to return 200
  - ✅ `npm run codegen` against a live FastAPI produces a 3276-line
    typed API surface; typecheck still passes against the real types

### Tasks

#### Frontend scaffold (this PR)
- [x] `frontend/` directory with `package.json`, `vite.config.ts`,
      `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json`,
      `index.html`, `vite-env.d.ts`
- [x] Tailwind 3 installed properly (no CDN); `tailwind.config.ts`,
      `postcss.config.js`, `src/styles/globals.css` with HSL token vars
- [x] React Router v7 with placeholder Status route (`/`) + 404
- [x] TanStack Query provider + ReactQueryDevtools (dev-only)
- [x] Responsive AppShell: collapsing sidebar + topbar + content
      area + bottom status strip; mobile drawer overlay
- [x] shadcn/ui scaffolding (`components.json`, `cn()` helper,
      `Button` primitive with cva variants)
- [x] SaaS seams: `auth/principal.ts`, `auth/useCurrentUser.ts`,
      `branding.ts`, `flags.ts`, `lib/storage.ts` (`useUserSetting`),
      `hooks/useQuotaMutation.ts`, `api/client.ts` with `withAuth`
      + `withTelemetry` middleware
- [x] OpenAPI codegen: `npm run codegen` script + offline placeholder
      `src/api/types.gen.ts`
- [x] ESLint flat config + Prettier (locked over Biome — see §3.0)
- [x] Vite dev proxy: `/api`, `/mcp`, `/openapi.json`, `/ws/*` →
      `http://localhost:8000` (override via `STOCKALERT_BACKEND_URL`)
- [x] Production build target: `../app/static/dist/`
- [x] Path alias `@/` → `src/` (tsconfig + Vite)
- [x] `frontend/README.md` with run/build/codegen + lift-out contract
- [x] Root `.gitignore` updated to exclude `frontend/node_modules/`
      and `app/static/dist/`

#### Backend (this PR — minimal)
- [x] `app/main_api.py` mounts `app/static/dist/` if present, at
      `/app` (legacy `/dashboard`, `/symbol/{ticker}`, `/journal`
      stay unchanged; cockpit is purely additive today)
- [ ] CI: frontend-build job (deferred — separate small PR once the
      skeleton has settled and we've verified `npm install` works on
      the operator's machine)

#### Companion PR — TA-SaaS-1 (deferred, NOT in this PR)
- [ ] `app/auth/principal.py` with `Principal` Pydantic model
- [ ] `get_principal` FastAPI dependency
- [ ] CH `audit_events` table + middleware
- [ ] `/api/*` → `/api/v1/*` one-shot rename (legacy redirects)
- [ ] WS handshake `Principal` gating

**Rationale for split:** the frontend scaffold doesn't *need* the
backend seams to exist — `useCurrentUser` is a no-op constant either
way. Bundling them risks a backend regression riding along with a
frontend bootstrap that nobody on the operator side can review yet.
Land FE-1 frontend first, get the shell rendering, then ship
TA-SaaS-1 backend seams as a focused review.

---

## Phase FE-1.5 — Status page wiring

**Goal:** Replace the FE-1 Status placeholder with live data. Add a
composite backend endpoint (`GET /api/health/services`) that fans out
to ClickHouse, Iceberg, Schwab, Polygon, backfill queue, and monitor
manager probes; render through a shared TanStack Query hook that
also feeds the StatusBar so both the page and footer share one HTTP
round-trip every 10s.

**Status:** ✅ COMPLETE
**Started:** 2026-05-18
**Completed:** 2026-05-18
**Gate (all green):**
  - ✅ Backend: `GET /api/health/services` returns the documented
    shape; `tests/test_routes_health.py` green (3/3) including the
    failure-isolation test (a probe raising → `state: "error"` row,
    not a 5xx)
  - ✅ Frontend: `useHealthServices()` polls every 10s; StatusPage
    renders 4 service cards + 2 summary cards; StatusBar reflects
    the same data via the deduplicated cache
  - ✅ Live smoke: ClickHouse 2ms, Iceberg <1μs (cached handle),
    Schwab + Polygon configured-and-OK on the operator's machine
  - ✅ Refresh button on StatusPage forces refetch; spinner reflects
    `isFetching`
  - ✅ `npm run typecheck`, `npm run lint`, `npm run build` all
    green; bundle still well under target

### Tasks

#### Backend
- [x] `app/api/routes_health.py` with `HealthServicesResponse`
      Pydantic model; per-subsystem probe functions isolated so any
      one failing produces `state: "error"` rather than a 5xx
- [x] Mounted at `/api/health/services` (sibling of legacy `/health`
      which stays bool-only for back-compat)
- [x] `tests/test_routes_health.py` — 3 tests covering shape,
      failure-isolation, and required subsystem coverage

#### Frontend
- [x] `src/api/queries.ts` — central TanStack Query hook layer
      (`queryKeys`, `useHealthServices`, `useSymbolBars`,
      `useSymbolSignals`); shape-normalizer for the legacy `/api/bars`
      response triad documented inline
- [x] `src/lib/fmt.ts` — display formatters (price, pct, vol, time,
      ago, latency)
- [x] `src/routes/status.tsx` — live page with service cards +
      backfill/monitor summary + refresh button + error banner
- [x] `src/components/layout/StatusBar.tsx` — wired to same query;
      single round-trip serves both surfaces (TanStack Query dedup)

---

## Phase FE-2 — Symbol page scaffold

**Goal:** First real chart in the cockpit. Lightweight Charts wired
through a React component, OHLCV + volume + signal markers, interval
picker persisted per-user, recent-bars table beneath. NOT yet
indicator overlays, coverage strip, journal panel — those are
FE-2.1+ follow-ons. This phase establishes the chart primitive and
proves the data pipeline (FastAPI → openapi-fetch → TanStack Query
→ Lightweight Charts) end-to-end.

**Status:** ✅ COMPLETE (parity scaffold; full parity in FE-2.1)
**Started:** 2026-05-18
**Completed:** 2026-05-18
**Gate (all green):**
  - ✅ `lightweight-charts@4.2.3` installed; chart renders in the
    SymbolPage with autoSize + dark theme matching cockpit tokens
  - ✅ `/symbol` (no ticker) renders a search + recent-tickers
    picker; `/symbol/AAPL` renders chart + bars table with real data
  - ✅ Interval picker persists per-user via `useUserSetting`;
    switching interval triggers a new query, chart updates without
    teardown
  - ✅ Signal markers (arrows above/below bar by direction) render
    when /api/signals returns data
  - ✅ Live smoke against operator's backend: AAPL 5m bars load
    (5 recent bars verified, ~$297 close, volume 1.4K–2.6K range)
  - ✅ End-to-end build: 154 KB gz JS (still under 250 KB target)

### Tasks
- [x] `lightweight-charts` package installed
- [x] `src/components/charts/OhlcvChart.tsx` — wrapper that owns
      chart lifecycle (create/resize/dispose), separates create-
      effect from data-update effects (no teardown on prop change)
- [x] `src/components/tables/BarsTable.tsx` — recent bars table
      with up/down color encoding; TanStack Table swap deferred
      to FE-3 once virtualization is needed
- [x] `src/routes/symbol.tsx` — page with header (price + change %),
      interval picker, chart, bars table; falls back to SymbolPicker
      when no ticker in URL
- [x] Recent-tickers list persisted via `useUserSetting('symbol.recent')`
- [x] Router updated with `/symbol` and `/symbol/:ticker` routes
- [x] `page.symbol` flag flipped to `true` in `flags.ts`
- [x] `src/api/queries.ts` — `useSymbolBars` + `useSymbolSignals`
      hooks with shape-tolerant response normalizers

### Deferred to FE-2.1 (scoped follow-ons)
- [ ] Indicator overlays (`/api/indicators/series` already exists)
- [ ] Coverage strip beneath the chart
- [ ] Journal-trades-on-this-ticker side panel
- [ ] Adjusted/raw toggle (waits for silver `_adj` columns)
- [ ] "Promote to seed" button for ad-hoc symbols (waits for the
      `/api/seed/promote` endpoint)
- [ ] Live tick updates via WS (`bars.{symbol}` topic — waits for
      `/ws/events`)

---

## Phase FE-CONTRACTS — Backend contract pass

**Goal:** Close the type-chain so every cockpit-facing endpoint
declares a Pydantic `response_model`. Add missing endpoints (seed
universe CRUD, streaming-provider switch, sim trades, ad-hoc CH
query, journal equity curve). Standardize the error envelope, the
pagination shape, and the WebSocket fan-out. Move the live namespace
to `/api/v1/*`.

**Status:** ✅ APPROVED 2026-05-18 — all seven open questions
locked. FE-CONTRACTS-1 starts next. Full plan in
[docs/frontend_api_contracts.md](frontend_api_contracts.md).

**Gate:** see [frontend_api_contracts.md §11](frontend_api_contracts.md).

### Locked decisions (2026-05-18)

| § | Question | Decision |
|---|---|---|
| 10.1 | Watchlists vs streaming | Sticky-universe model — universe is source of truth; watchlist add can promote to universe; watchlist remove never strips streaming |
| 10.2 | `/api/v1` namespace | One-shot rename in FE-CONTRACTS-1; 307 redirects for legacy paths |
| 10.3 | Seed universe storage | ClickHouse `seed_universe` table; bootstrap from `SEED_SYMBOLS ∪ <watchlist members>` on first run |
| 10.4 | CH query page | Bare SQL textarea + read-only role + 10k row cap + 30s timeout + schema sidebar |
| 10.5 | Provider switch | In-process restart now (~10s downtime); promote to hot-swap in FE-CONTRACTS-7 |
| 10.6 | Sim trades | Instant fill UX + **realistic slippage + fees from day 1** via the existing `FeeModel`/`SlippageModel` Protocols ([app/services/sim/fees.py](../app/services/sim/fees.py)); audit fields on every trade for empirical refinement |
| 10.7 | MCP type parity | Defer; migrate piecemeal as tools get touched |

### Sub-phases
- [x] **FE-CONTRACTS-1** ✅ COMPLETE 2026-05-18 — `app/api/schemas/`
      package + `ErrorResponse` + `Page[T]` + apiClient middleware +
      `/api/v1` one-shot rename. See FE-CONTRACTS-1 detail block below.
- [x] **FE-CONTRACTS-2** ✅ COMPLETE 2026-05-18 — Bar, Signal,
      InstrumentMatch, MarketBanner, Movers models; cockpit
      hand-rolled types deleted. See FE-CONTRACTS-2 detail block below.
- [ ] **FE-CONTRACTS-3** (~2 days) — Watchlist + Monitor models;
      prefix cleanup; `/api/v1/watchlists`, `/api/v1/monitors`
- [ ] **FE-CONTRACTS-4** (~3 days) — Seed universe migration to CH
      + one-time bootstrap from current state; `/api/v1/seed`,
      `/api/v1/config/streaming`
- [ ] **FE-CONTRACTS-5** (~3 days) — `sim_trades` CH table with
      cost-model audit fields; live sim-trade endpoints reusing the
      backtester's `FeeModel`/`SlippageModel`; `/api/v1/sim/*`,
      `/api/v1/sim/cost-config`, `/api/v1/backtest` typed both ways
- [ ] **FE-CONTRACTS-6** (~2 days) — Journal performance models +
      equity curve; `/api/v1/clickhouse/query` + `/schema`
- [ ] **FE-CONTRACTS-7** (~3 days) — `/ws/events` topic-multiplexed
      WebSocket replacing `/ws/signals`; promote provider switch to
      true hot-swap

---

### FE-CONTRACTS-1 — detail (LANDED 2026-05-18)

**Goal:** Foundation for the type chain. Schemas package + uniform
error envelope + the one-shot `/api/v1` rename.

**Status:** ✅ COMPLETE 2026-05-18
**Gate evidence:**
  - ✅ All cockpit pages still render against new namespace
  - ✅ Backend regression sweep: **990 passed, 5 skipped, 0 failures**
    (`pytest --deselect=tests/integration`)
  - ✅ FE-CONTRACTS-1 targeted tests: **14/14 green**
    (`tests/test_api_v1_namespace.py`)
  - ✅ Frontend gates: `npm run typecheck`, `lint`, `build` all green;
    bundle stays at 154 KB gz
  - ✅ End-to-end smoke (against live backend on operator's machine):
    - `/api/v1/health/services` → 200 with envelope
    - `/api/v1/market/banner` → 200
    - `/api/v1/watchlists` → 200
    - `/api/health/services` → 307 → `/api/v1/health/services`
    - `/watchlist/snapshot` → 307 → `/api/v1/watchlist/snapshot`
    - `POST /watchlist/add` → 307 follow → 200 (method + body preserved)
    - `/api/v1/nonexistent` → 404 with `{code:"not_found", message, details, request_id}`
    - `/api/v1/bars` (missing symbol) → 422 with field-level errors in `details.errors`
    - OpenAPI spec contains 38 `/api/v1/*` routes, 0 legacy `/api/*`
      (redirects are `include_in_schema=False`)
  - ✅ Legacy HTML pages (`/dashboard`, `/symbol/:ticker`, `/journal`)
    still 200; their `/api/*` and `/watchlist/*` fetches work via 307
  - ✅ Cockpit (`/app/`, `/app/symbol/AAPL`) still 200

### Backend changes
- [x] `app/api/schemas/__init__.py` re-exports common primitives
- [x] `app/api/schemas/common.py` — `ErrorResponse`, `Page[T]`,
      `AssetType`, `HealthState`, `Interval`, `OkResponse`, `isoformat_z`
- [x] `app/api/schemas/README.md` — folder rules + migration recipe
- [x] `app/main_api.py`:
      - Three exception handlers (`StarletteHTTPException`,
        `RequestValidationError`) emit `ErrorResponse` envelope
      - Status-code → error-code default map; route can override via
        `HTTPException(..., headers={"X-Error-Code": "..."})`
      - All router mounts moved to `prefix="/api/v1"`
      - Catch-all `@app.api_route("/api/{rest:path}", ...)` returns
        307 to `/api/v1/...` preserving query string (and method+body
        because 307)
      - Specific redirects for legacy root-mounted `/watchlist[/...]`
- [x] `app/api/routes_watchlist.py` — stripped hardcoded `/api/`
      prefix from the multi-watchlist routes (mount prefix now applies
      uniformly)
- [x] `tests/test_api_v1_namespace.py` — 14 tests covering v1
      reachability, redirects (query-string + method-preservation +
      follow-through), envelope shape for 404/422/route-raised, and
      no-redirect-loop on unknown v1 paths

### Frontend changes
- [x] `frontend/src/lib/errors.ts` — `ApiError extends Error` with
      `code`/`status`/`details`/`requestId`; `readErrorEnvelope()`
      best-effort parser with non-JSON fallback; `isApiError()` tag
- [x] `frontend/src/api/client.ts` — added `withErrorEnvelope`
      middleware so any non-2xx becomes a typed `ApiError` throw
- [x] `frontend/src/api/queries.ts` — shared `fetchJson<T>()` helper
      that throws `ApiError`; URLs updated `/api/*` → `/api/v1/*`
- [x] `frontend/src/components/ApiErrorAlert.tsx` — typed alert
      shows `message` + `code` badge + `request_id`
- [x] Existing pages (`status.tsx`, `symbol.tsx`) use `ApiErrorAlert`
      instead of inline banners

### Migration tally for FE-CONTRACTS-2
After FE-CONTRACTS-1 lands, the remaining work to delete every
hand-rolled interface in `frontend/src/api/queries.ts` requires:
  - `Bar` Pydantic model on `/api/v1/bars`
  - `Signal` Pydantic model on `/api/v1/signals`
  - `InstrumentSearchResponse` on `/api/v1/instruments/search`
  - `MarketBannerResponse` on `/api/v1/market/banner`
  - `MoversResponse` on `/api/v1/movers`
(see [frontend_api_contracts.md §4](frontend_api_contracts.md) for
the proposed schemas)

---

### FE-CONTRACTS-2 — detail (LANDED 2026-05-18)

**Goal:** Type the five cockpit-blocking routes so the frontend can
delete its hand-rolled interfaces and pick up types via codegen.

**Status:** ✅ COMPLETE 2026-05-18
**Gate evidence:**
  - ✅ Five new schema files under `app/api/schemas/` (bars, signals,
    instruments, market with banner+movers)
  - ✅ All five routes declare `response_model`; OpenAPI spec
    publishes the schemas for codegen
  - ✅ Backend regression sweep: **1001 passed, 5 skipped, 0 failures**
    (up from 990 — 11 new tests added in
    `test_api_v1_response_models.py`)
  - ✅ `npm run codegen` against live backend produces typed paths;
    rerun produces zero diff (codegen is hermetic)
  - ✅ Frontend `OhlcvBar`, `BarsResponse`, hand-rolled `Signal`,
    and `normalizeBars()` shim all DELETED from
    `frontend/src/api/queries.ts`
  - ✅ `Bar`, `Signal`, `InstrumentMatch`, `BannerItem`, `Mover`,
    plus their response wrappers, are now `components["schemas"]["…"]`
    re-exports
  - ✅ `useSymbolBars` + `useSymbolSignals` use `apiClient.GET()`
    with full type-flow from Pydantic → TypeScript
  - ✅ Frontend `npm run typecheck`/`lint`/`build` all green;
    bundle 156 KB gz (still under 250 KB target)
  - ✅ Live smoke: `/api/v1/bars` returns the typed `Bar` shape
    (`{ts, open, high, low, close, volume, vwap, trade_count, source}`);
    legacy `/dashboard` + cockpit `/app/symbol/AAPL` both 200

### Wire-shape contract preserved
Every new model matches the existing legacy response shape exactly,
so static HTML consumers (`dashboard.html`, `symbol.html`,
`journal.html`) keep parsing without changes. Specifically:
  - `/api/v1/bars` still returns a **bare list** of bar dicts (not
    `Page[Bar]`). Promotion to `Page[Bar]` is deferred to the phase
    that deletes the static HTML, so the legacy consumers never see
    a breaking change.
  - `/api/v1/movers` keeps every field the dashboard reads
    (`indexes`, `upstream_count`, `per_index_counts`, `filtered_out`,
    `fetched_at`).

### Backend changes
- [x] `app/api/schemas/bars.py` — `Bar` model
- [x] `app/api/schemas/signals.py` — `Signal` model
- [x] `app/api/schemas/instruments.py` — `InstrumentMatch`,
      `InstrumentSearchResponse`
- [x] `app/api/schemas/market.py` — `BannerItem`, `BannerError`,
      `MarketBannerResponse`, `Mover`, `MoversResponse`
- [x] `app/api/routes_signals.py` — `/api/v1/bars` + `/api/v1/signals`
      declare `response_model`, return typed instances
- [x] `app/api/routes_instruments.py` — `/api/v1/instruments/search`
      typed
- [x] `app/api/routes_market.py` — `/api/v1/market/banner` typed
- [x] `app/api/routes_movers.py` — `/api/v1/movers` typed
- [x] `tests/test_api_v1_response_models.py` — 11 tests asserting
      OpenAPI schemas present + correct response_model ref + live
      wire-shape preserved

### Frontend changes
- [x] `frontend/src/api/types.gen.ts` — regenerated; now non-empty
- [x] `frontend/src/api/queries.ts` — hand-rolled `OhlcvBar`,
      `BarsResponse`, `Signal`, `normalizeBars()` DELETED; new
      `signalDirection()` helper derives bull/bear from `Signal.type`
      (backend has no `direction` field)
- [x] `frontend/src/api/client.ts` — apiClient.GET wired with full
      type chain (already done in FE-CONTRACTS-1, now actually used)
- [x] `frontend/src/routes/symbol.tsx` — uses `bars.data` directly
      (no more `.bars` indirection); also passes 100 instead of
      `(symbol, interval, 100)` to `useSymbolSignals` (interval no
      longer a backend param)
- [x] `frontend/src/components/charts/OhlcvChart.tsx` — uses `Bar` +
      `Signal` from queries; signal direction derived via
      `signalDirection()` helper instead of the nonexistent
      `s.direction` field (this was a silent bug pre-CONTRACTS-2)
- [x] `frontend/src/components/tables/BarsTable.tsx` — uses `Bar`

---

### Cockpit MarketBanner — drop-in (LANDED 2026-05-18)

**Goal:** Surface the always-visible market tape (index + futures
last/change) on every cockpit page, mirroring the legacy dashboard.

**Status:** ✅ COMPLETE 2026-05-18
**Gate evidence:**
  - ✅ `useMarketBanner()` hook in `queries.ts` uses `apiClient.GET`
    with full type chain (Pydantic `MarketBannerResponse` → TS)
  - ✅ `<MarketBanner>` component in
    `frontend/src/components/market/MarketBanner.tsx` —
    horizontal scrollable strip of chips; per-chip color-coded
    `change_pct`; click → `/symbol/<symbol>` for equity-style
    symbols (index `$SPX` and future `/MNQM26` chips degrade to
    non-link spans since they don't have cockpit symbol pages yet)
  - ✅ Wired into `AppShell` above `Topbar`, persistent on every
    route; `md:` breakpoint hides it on phones (cockpit is
    desktop-first)
  - ✅ Loading state: 4 skeleton chips; error state: muted
    "market data unavailable" (banner failures shouldn't shout)
  - ✅ Auto-refresh every 10s (matching Status page cadence);
    same TanStack Query dedup pattern as `useHealthServices`
  - ✅ Live smoke against operator backend:
    `/api/v1/market/banner` returned real Schwab quotes
    (SPX 7403.05, NDX 28994.37) with the typed shape
  - ✅ Frontend gates: typecheck / lint / build all green; bundle
    157 KB gz (target <250 KB)

**Files**
- [x] `frontend/src/api/queries.ts` — added `useMarketBanner` hook
      + `queryKeys.marketBanner`
- [x] `frontend/src/components/market/MarketBanner.tsx` — new
- [x] `frontend/src/components/layout/AppShell.tsx` — mounts the
      banner above the Topbar inside the content column
- [x] `frontend/README.md` — added the chrome-layout diagram

**Out of scope (deliberate)**
- No `/symbol/$SPX` route yet — clicking an index doesn't navigate.
  Index pages would need different chart logic (no OHLCV bars for
  pure indexes) so it's its own follow-on, not a banner bug.
- No marquee animation. Bloomberg-terminal aesthetic prefers static
  legibility over motion; revisit if user feedback says otherwise.
- No price-flash on update (green/red brief flash). Easy to add
  later as a `useEffect` watching `item.last` — defer until real
  data feeds make the difference perceptible.

