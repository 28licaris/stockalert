# StockAlert — Issue Tracker

In-repo issue list for bugs, flaky tests, blockers, and follow-up
cleanup. Lighter than GitHub Issues; lives in-repo so context goes
with the code and PRs can cite IDs.

For longer-running work (phases, multi-step initiatives), use commit
messages and the [`architecture/`](architecture/README.md)
phase docs. This file is for the "things that are wrong" list.

## How to file an issue

1. **Pick an ID** in `kebab-case` that names the thing concisely.
   Examples: `schwab-chart-fields-test-drift`, `bronze-compaction-glacier-fee`,
   `dashboard-banner-flicker-on-reconnect`. Avoid dates and ticket
   numbers — IDs outlive both.
2. **Pick one or more area tags** (see the taxonomy below).
3. **Add a new section at the top of `## Open`** using the entry
   template. New items go on top; older items sink down.
4. **Reference the ID in PRs / commits** that touch it
   (`fix(schwab-chart-fields-test-drift): align test fixtures`).
5. **When fixed, move the entry to `## Resolved`** with the date and
   PR/commit link. Do not delete — short history helps when something
   regresses.

If you're not sure whether something is an issue or a backlog item:
issues are "this is broken / known wrong"; backlog items are "this is
fine, we'd just like more of it." Backlog goes in commit messages
or the relevant phase doc under `architecture/`.

## Area tags

Use one or more, comma-separated. Add new tags here as new areas appear
— don't invent ad-hoc tags in entries without listing them here.

| Tag | Covers |
|---|---|
| `provider:polygon` / `provider:schwab` / `provider:alpaca` | Provider-specific code paths |
| `bronze` / `silver` / `gold` | Iceberg lake tiers |
| `indicators` / `signals` | TA layer (`app/indicators/`, `app/signals/`) |
| `ingest` | Nightly + backfill jobs (`app/services/ingest/`) |
| `live` | Watchlist, monitor service, streaming (`app/services/live/`) |
| `journal` | Trade-journal sync + parser (`app/services/journal/`) |
| `db` | ClickHouse schema, queries, batcher |
| `api` | FastAPI routes (`app/api/`) |
| `ui` | React dashboard (`app/static/`, frontend source) |
| `mcp` | Agent-facing tools (`app/mcp/` — Phase Pre-3 Step 3) |
| `infra` | AWS, Glue, Athena, Docker, IAM, lifecycle |
| `tests` | Flaky, drifted, or pre-existing broken tests |
| `docs` | Documentation drift, missing READMEs |

## Entry template

Copy this block when filing a new issue:

```markdown
### `<kebab-case-id>`

- **Area:** tag, tag
- **Filed:** YYYY-MM-DD
- **Status:** open
- **Symptom:** what the user / CI / agent actually sees. Concrete:
  filename + line, error message, query that fails.
- **Root cause:** best current understanding. Leave as
  `unknown — needs investigation` if not yet diagnosed.
- **Suggested fix:** one-line plan or `unknown`.
```

Status values: `open`, `in-progress`, `blocked`, `wontfix`.
Use `blocked` when waiting on an external thing (vendor fix, AWS quota
increase, etc.) and note what's blocking.

---

## Open

### `futures-stream-bars-stamped-at-arrival`

- **Area:** provider:schwab, live, db
- **Filed:** 2026-11-05
- **Status:** open (worked around in the lake archive; provider not changed)
- **Symptom:** ClickHouse `futures_ohlcv_1m` rows with `source='schwab-stream'` carry
  timestamps like `15:01:03.228`, while the REST-backed lake labels the same bar
  `15:00:00`. Measured 2026-11-05: 98% of stream bars price-match the lake bar at
  -1 minute. In ClickHouse the stream row and the `lake-reconcile` row for one bar
  therefore have different keys, so a futures chart can show near-duplicate bars.
- **Root cause:** `SchwabProvider._chart_content_to_bar` uses CHART_TIME (field 7).
  For CHART_EQUITY it is the minute start; for CHART_FUTURES it is bar end plus
  1-3 s. Equities are unaffected.
- **Suggested fix:** normalise futures stream timestamps to `floor_minute(ts) - 1min`
  in the provider (the lake archive already does, `BarFamily.bar_minute_offset`),
  then re-key the existing CH rows. Needs a spec: it changes stored data.

### `live-stream-silent-stall-reports-healthy`

- **Area:** live, provider:schwab, api
- **Filed:** 2026-11-05
- **Status:** open
- **Symptom:** on 2026-11-05 the laptop streamer delivered nothing after 16:07 UTC
  (mid-session) while `/api/v1/stream/status` reported `started=true`,
  `provider_ready=true`, `streaming_count=431`, and the freshness page labelled the
  6-hour-old live bars "outside expected window (normal)". The lake archive audit
  showed the same pattern on 2026-11-03 (2,766 stream rows vs ~127k expected).
- **Root cause:** unknown, needs investigation. Status counts subscriptions, not
  delivered bars (same blind spot as `schwab-stream-300-symbol-account-cap`).
- **Suggested fix:** health check that compares bars delivered in the last N minutes
  with subscribed symbols during market hours, and alert; make the freshness
  "expected window" aware of the session calendar.


### `schwab-stream-300-symbol-account-cap`

- **Area:** provider:schwab, live, ingest
- **Filed:** 2026-11-03
- **Status:** open — **confirmed, and NOT fixed by chunking**
- **Symptom:** only **300** equity symbols ever receive live bars, however many
  are subscribed. `/stream/status` reports all of them as subscribed.
- **Confirmed by intervention, not inference.** Two separate theories were
  tested and the second was wrong:

  1. *Account entitlement cap near 290-300* — close, but imprecise.
  2. *Per-request truncation of the SUBS key list* — **disproved.** PR #34
     chunked subscriptions at 300 keys (first chunk `SUBS`, rest `ADD`) on the
     theory that one oversized message was being truncated. The `ADD` chunk of
     115 keys was accepted at protocol level and **delivered nothing**. The
     ceiling did not move.

  The arithmetic closes exactly, which is what settles it:

  ```
  before: 424 subscribed, 7 dead symbols inside the first 300 keys
          300 accepted - 7 dead = 293 streaming      (observed 293)
  after : 415 subscribed, dead removed
          300 accepted - 0 dead = 300 streaming      (observed 300)
  ```

  Removing the 7 dead entries freed exactly 7 slots. **The cap is a hard 300
  concurrent symbols on CHART_EQUITY for this account** — a subscription-level
  limit, not a message-size one. CHART_FUTURES is unaffected (16 roots stream
  fine to `futures_ohlcv_1m`).

- **What PR #34 did and did not do:** it did NOT fix this. It is still worth
  keeping — it fixed a genuine latent bug (`subscribe_bars` sent `SUBS` with
  only the delta while accumulating the full set, so any incremental subscribe
  after login would have silently unsubscribed everything prior), and it is the
  shape the code needs if the cap is ever raised. But 115 symbols still receive
  no live bars.
- **Real options**, none of them free:
  1. **Trim to 300.** Prioritize the research core; accept that the rest are
     daily-only. Simplest, works today.
  2. **Rotate** subscriptions across the overflow. Adds gaps and complexity.
  3. **Ask Schwab** whether the tier supports a higher cap or a second
     streaming connection. Unknown; needs them.
  4. **REST-poll the overflow.** `historical_df` is NOT subject to this cap
     (proved: SPY/TSLA/XLF return full history via REST while silent on the
     stream). `schwab_daily_refresh.py` already relies on that for daily bars;
     the same path could fill intraday for the overflow at lower frequency.
- **Related, and the deeper defect:** `subscribed` is not `receiving`, and
  nothing compares them. That is why this hid for three months. The health
  panel should surface delivered-vs-subscribed per service.

### `schwab-dead-universe-entries` — RESOLVED 2026-11-03

Nine symbols returned `{"symbol": X, "empty": true, "candles": []}` from Schwab
for **all** history including 2024 — AVB, BK, DFS, EA, EQR, K, MMC, SQ, WBD.
Not a cap (a cap cannot explain a 2024 query), not transient. Confirmed
renames: `SQ -> XYZ` (Block), `DFS -> COF` (acquired). Deactivated in
`stream_universe` with the reason in `notes`; active universe 424 -> 415.

### `schwab-token-expiry-masked-by-live-stream`

- **Area:** provider:schwab, ingest, options
- **Filed:** 2026-11-03
- **Status:** open (operational trap; recovery documented, no code fix yet)
- **Symptom:** every Schwab REST call fails with
  `{"error_description":"Refresh token is invalid, expired or revoked"}` while
  the dashboards look healthy. Cost three months of option-chain collection
  (2026-08-12 → 2026-11-03): the snapshot loop ran every 15 minutes and errored
  every time, and nobody noticed.
- **Why it hides:** the **live streamer keeps working**. Its websocket
  authenticates once at connect and stays up, so `ohlcv_1m` keeps flowing and
  every freshness check stays green while REST is completely dead. *A healthy
  stream is not evidence that Schwab auth is healthy.*
- **Two traps in the recovery**, both hit in practice:
  1. `SCHWAB_REFRESH_TOKEN` in `.env` **overrides** `SCHWAB_REFRESH_TOKEN_FILE`.
     Re-auth writes `data/.schwab_refresh_token` and the stale `.env` value
     silently wins.
  2. **Each worktree has its own `.env` and `data/`.** Re-authorizing in the
     primary checkout does nothing for a worktree, which keeps failing with the
     old token and looks like the re-auth failed.
- **Recovery:** `python scripts/schwab_get_refresh_token.py` (interactive
  browser OAuth), clear/update the `.env` override, copy the token to any
  worktree that needs it, then **restart uvicorn** — the process caches the
  token at startup.
- **Suggested fix:** surface REST auth health separately from stream health on
  the health page, and alert when a scheduled job's `last_status` has been
  `error` for N consecutive runs. A job failing silently for three months is the
  real defect; the token expiring is normal.

### `options-snapshot-single-writer-only`

- **Area:** data, infra, options
- **Symptom:** with the options snapshot loop enabled on two servers at once,
  **both** fail. Concurrent Iceberg appends to `options.schwab_chain_raw` /
  `schwab_chain_contracts` hit optimistic concurrency —
  `Requirement failed: branch main has changed: expected id X, found Y` — and
  the losing writer aborts the whole symbol. Measured 2026-08-12: all 40
  symbols errored on both sides with `rows_written=0`, so even the raw payload
  archive (the recovery source for any parser defect) stopped being written.
- **Why it matters:** two writers is strictly *worse* than one. One writer with
  a bug still archives raw payloads, which are replayable. Two writers archive
  nothing, and that data is gone.
- **Workaround:** exactly one process may set `OPTIONS_SNAPSHOT_ENABLED=true`.
  Disable it everywhere else before enabling it somewhere new.
- **Real fix:** the options sink needs commit-conflict retry with backoff, the
  way the equities sink already has. Until then this is an operational rule,
  not a guardrail the code enforces.

### `ppl-30m-interval-uncalibrated`

- **Area:** research, cockpit
- **Symptom:** `/ppl` defaults to the 30-minute interval, which is the one
  interval with **no** PIT calibration behind it. 1h and 1d are calibrated and
  pass; 30m has never been measured because the research table it needs
  (`ohlcv_30m`) was never built — `scripts/build_ohlcv_30m.py` exists and has
  never been run.
- **Why it matters:** the page's calibration banner speaks about the *model*,
  which is honest, but a reader could reasonably infer the default view is
  covered. It is not.
- **Fix:** run `scripts/build_ohlcv_30m.py`, then
  `scripts/ppl_calibrate.py --interval 30m`. If 30m fails the gate, either fix
  it or change the default to 1h.

### `cockpit-watchlists-page-no-default-selection`

- **Area:** ui
- **Filed:** 2026-05-19
- **Status:** open
- **Symptom:** Navigating to `/app/watchlists` shows the list of
  watchlists on the left but the right-hand detail panel renders
  the empty-state placeholder ("Select a watchlist on the left").
  The operator expected at minimum the `default` watchlist to be
  pre-selected — and ideally the most-recently-active one the user
  last interacted with.
- **Root cause:** `WatchlistsPage` in
  [frontend/src/routes/watchlists.tsx](../frontend/src/routes/watchlists.tsx)
  initializes `selected` as `null` and never auto-picks. The page
  only opens a detail panel after the operator clicks a row.
- **Suggested fix:** Two parts.
  1. **Now (FE-CONTRACTS-3 follow-up):** auto-select on first load
     using a fallback chain: `useUserSetting('watchlists.lastSelected')`
     → first item whose name matches a `default` constant → first
     item in the list. Persist `selected` to that
     `useUserSetting` key on every selection change so the choice
     survives reload.
  2. **Later (FE-11+ SaaS phase):** when real auth lands, move the
     same "last active watchlist" state from `localStorage` into
     a per-tenant prefs endpoint (`GET/PUT /api/v1/me/prefs`),
     keyed under `watchlists.lastSelected`. The cockpit's
     `useUserSetting` hook already abstracts this — the SaaS swap
     becomes a single-file change to its backing store.
- **Notes:** the `useUserSetting` seam already exists at
  [frontend/src/lib/storage.ts](../frontend/src/lib/storage.ts);
  this fix is genuinely additive (~10 lines on the page + one
  `useEffect`). Bundling it with the next FE-3 polish pass so
  the commit covers "Watchlists page UX rough edges" as a group.

### `ta2-live-anthropic-run-deferred`

- **Area:** tests, mcp
- **Filed:** 2026-05-17
- **Status:** open (operator-action required)
- **Symptom:** Phase TA-2 (LLM strategy + MCP `run_backtest`) is
  fully tested with stubbed Anthropic responses — 21 green tests
  including a replay-reproducibility regression. The live
  end-to-end verification against the real Anthropic API was
  deferred because no `ANTHROPIC_API_KEY` is present in
  `/Users/licaris/dev/stockalert/.env`.
- **Root cause:** missing operator-supplied credential. Not a
  code issue.
- **Suggested fix:** Add `ANTHROPIC_API_KEY=sk-ant-...` to the
  main `.env` (never paste into chat — use
  `echo 'ANTHROPIC_API_KEY=...' >> ~/dev/stockalert/.env`
  in the terminal). Then:
    ```
    poetry run python scripts/run_backtest.py \
      --config configs/llm_agent_smoke.yaml
    ```
  Expected cost: ~$0.05 for the smoke (45 trading days AAPL),
  ~$0.50 for the full year ([configs/llm_agent.yaml](../configs/llm_agent.yaml))
  if smoke passes. Replays from the local SQLite cache are $0.
  Verify health markers: `n_trades >= 1`, `api_failures == 0`,
  `parse_failures` low single digits, an `agent_runs` row written.

### `bronze-iam-missing-getlifecycleconfiguration`

- **Area:** infra
- **Filed:** 2026-05-14
- **Status:** open (not blocking)
- **Symptom:** IAM policy on `stock-lake-ingest` includes
  `s3:PutLifecycleConfiguration` but not `s3:GetLifecycleConfiguration`.
  Read-back of bucket lifecycle config from code returns AccessDenied.
- **Root cause:** Provisioner only needs Put; nothing today reads the
  lifecycle config programmatically, so it was omitted.
- **Suggested fix:** Add the Get action to the policy when we want a
  read-back path (e.g. an ops health check that verifies lifecycle is
  applied). Trivial one-line policy update.

---

## Resolved

<!-- format:
### `<id>` — resolved YYYY-MM-DD

Brief summary + commit / PR link.
-->

### `econ-ingest-release-detection-test-drift` — resolved 2026-11-04

Two `test_econ.py` ingest tests were **making live HTTP calls to
`apps.bea.gov`**. They injected a fake BLS client but not a BEA one, and
`EconService._gather` lazily builds a real `BeaClient` whenever
`BEA_API_KEY` is set — so the tests pulled real GDP/PCE data
(`series=3 points=62` instead of `series=1 points=2`) and failed their
assertions. They passed on any machine without the key, which is why they
read as "pre-existing failures" rather than as wrong tests.

`EconService` was correct and unchanged. Fix: inject a `_FakeBea` in both
tests so they are hermetic regardless of ambient env. Runtime dropped from
3.53s to 0.07s — the tell that they had been doing network I/O.

Rule added to [`standards/testing.md`](standards/testing.md): inject every
collaborator, not just the one under test.

### `schwab-chart-fields-test-drift` — resolved 2026-05-17

Three `TestChartContentToBar` / `TestDataProviderContract` fixtures
in `app/providers/tests/test_schwab_provider.py` were rewritten to use the
empirical CHART_EQUITY field map (`2=Open, 3=High, 4=Low, 5=Close,
6=Volume, 7=ChartTime(ms)`) that the production constant
`CHART_EQUITY_FIELDS = "0,2,3,4,5,6,7"` uses. Implementation
unchanged — it was already correct. A header comment in the test
class points back to the production constant so the rationale
travels with the code.

### `schwab-streamer-url-key-test-drift` — resolved 2026-05-17

`TestGetUserPrincipals::test_sets_streamer_url_from_dict_nested`
deleted (tested a legacy `streamerConnectionInfo` nested shape
production has zero support for).
`test_sets_streamer_url_from_list` rewritten as
`test_sets_streamer_url_from_streamer_info_uri_fallback` —
verifies the production parser's `uri` fallback under the
`streamerInfo[]` key, complementing the existing
`test_sets_streamer_url_from_streamer_info` (streamerSocketUrl
happy path).

### `pre-existing-test-collection-errors` — resolved 2026-05-17

`tests/test_alert_flow.py`, `tests/test_indicators.py`, and
`tests/test_websocket.py` deleted. All three referenced modules
that never existed in this repo (`app.services.alert_service`,
`app.indicators.rsi.calculate_rsi`, `app.main`) and tested
designs that have been superseded. Coverage of the equivalent
functionality:
- RSI: `app/indicators/tests/test_indicators_ta3.py` + the screener, MTF, and
  MCP-live test suites.
- Divergence: now at `app/signals/divergence.py`; covered by
  `app/mcp/tests/test_mcp_live.py` and
  `app/services/readers/tests/test_readers_unit.py`.
- Production websocket (`/ws/signals` on `app/main_api.py`):
  currently uncovered. Filed as separate follow-up — not the
  same thing the deleted scaffold was attempting.

### `schwab-pricehistory-period-window-conflict` — resolved 2026-05-17

Two findings, both addressed:

**1. Code fix (correctness).** `historical_df` in
[app/providers/schwab_provider.py](../app/providers/schwab_provider.py)
used to send `period` alongside explicit `startDate`/`endDate` for
`periodType=day` (minute charts). Per
[docs/reference/schwab-api/market_data_api.md](reference/schwab-api/market_data_api.md):
*"If not specified startDate will be (endDate - period) ..."* —
`period` is purely a default-startDate derivation, ignored when
`startDate` is explicit. Sending both can confuse Schwab's internal
date math. Dropped `period` entirely from the request payload;
pinned the new behavior with
`app/providers/tests/test_schwab_provider.py::TestHistoricalDf::test_single_day_window_does_not_send_period`
and updated the existing assertion in
`test_uses_market_data_base_url_and_symbol_param` from
`period == 1` → `"period" not in params`.

**2. Root cause of the original 2026-04-03 gap (not a code bug).**
Live-tested against Schwab: Apr 2 (Thu) and Apr 6 (Mon) return data
normally; Apr 3 returns HTTP 400 with the same inverted-date error
even after the code fix. **April 3, 2026 was Good Friday — US
equity markets were closed.** Schwab's pricehistory returns a
confusing 400 for closed-market days instead of empty bars. The
provider's `_market_data_get` already maps non-200 → `{}`, so
`historical_df` returns an empty DataFrame and the backfill simply
counts "0 rows" for that day (with one ERROR log line). There's no
data to backfill — Polygon also doesn't have minute bars for
Apr 3 2026 because no trades occurred.

Net: code is now spec-compliant; the perceived missing day was a
market holiday. Resolved by [commit on main, push pending].

### `watchlist-repo-containing-test-failure` — resolved 2026-05-17

`tests/integration/test_watchlist_repo.py::test_watchlists_containing`
asserted `watchlists_containing("QQQ") == [b]` (exact equality)
but the production-seeded `default` watchlist also contains QQQ
(seeded by `migrate_default_watchlist` on app startup; CH is
shared with the running app, not an isolated test DB). Rewrote
the assertion as containment (`b in qqq_containers and a not in
qqq_containers`), matching the resilient pattern already used by
`test_list_all_active_symbols_filters_by_kind` above it.

## gex-history-endpoint-slow (2026-07-06)
`GET /api/v1/options/gex` (total-level, full 2017-2026 window) takes ~18s —
PyIceberg lake scan per request. The GEX page's history panel eats this on
first load (then react-query caches 5 min). Proper fix per platform
pattern: hot-tier the derived GEX series in ClickHouse (like bars) and
route reads through a gateway with a CH-first/lake-fallback selector. Do
NOT cache-band-aid in the API layer. Matters once the GEX page gets real
traffic; fine for research use now.

## schwab-refresh-token-silent-expiry (2026-08-04, MITIGATED)
Schwab refresh tokens live ~7 days. Twice (2026-07-03, 2026-07-20) one
expired unnoticed: the health check only asserted the token was PRESENT,
so the Status page stayed green while the intraday tier froze. The
2026-07-20 expiry cost `fomc_drift_hourly_spy` its first live FOMC
meeting (paper run stalled at computed_through 2026-07-20; the daily
strategy was unaffected because ohlcv_daily comes from Polygon REST, not
the Schwab stream).
Mitigation: `_check_schwab` now reports token AGE — warn ≥5d, error
≥6.5d — for both token sources (file mtime; env tokens via a
hash-keyed first-seen ledger at data/.schwab_token_seen.json, which
stores a hash, never the token). Tests: tests/test_schwab_token_age_health.py.
LIMITATION (by construction): for an env token first seen NOW, the
ledger clock starts now — a token that is ALREADY expired reads "0.0d,
ok" until the next re-auth stamps a true start. The complementary signal
is data freshness (stale intraday bars during market hours); wiring a
freshness alarm into the same health surface is the remaining gap.

## equities-sink-stale-table-handle (2026-08-05, FIXED)
`EquitiesIcebergSink` cached its PyIceberg table handle and never
refreshed before `append`. Iceberg commits are optimistic against the
metadata the handle was opened with, so ANY intervening commit — the
sink's own previous write, the live stream, a nightly job — left it
stale and every later append died with "branch main has changed".
Symptom: a 12-day Schwab gap backfill landed **1 day** (the first) and
reported errors for the rest; a long-running writer degrades to
"first write wins" silently. Fix: best-effort refresh before each
append (a transient refresh error must NOT fail an otherwise-good
write — pre-existing contract) + up to 4 attempts with exponential
backoff on `CommitFailedException`, then surface the error. Tests:
`test_repeated_writes_refresh_the_handle`,
`test_transient_conflicts_retry_until_success`,
`test_persistent_commit_conflict_still_errors`.

## thetadata-history-rejects-current-day (2026-08-06, HANDLED)
The v3 history endpoints refuse the CURRENT calendar day when
`expiration=*`: "Cannot fetch current-day data without specifying an
expiration". It is a hard rule about *today*, not about whether the
17:15 ET EOD report exists — a time-of-day margin does not help (tried;
36 units still failed). `thetadata_backfill.py` now caps every fetch
window at **yesterday ET**, and leaves the current month UNMARKED so a
later run picks up its remaining days (bronze appends; the GEX
derivation dedups per contract-day). Today's chain is reachable only via
the `/snapshot` endpoints. This makes the backfill safe to schedule
nightly.

## ch-reconcile-inserts-duplicate-keys (2026-08-06, BENIGN)
`reconcile_ch_from_schwab(lookback_days=N)` re-pushes the whole window,
so days already in ClickHouse are inserted again (Aug-4 went 113k →
227k rows). `stocks.ohlcv_1m` is `ReplacingMergeTree(version)` ordered
by (symbol, timestamp), so the duplicates are same-key and collapse on
merge — verified 226,906 raw rows vs 113,453 unique keys, and
`OPTIMIZE TABLE ohlcv_1m PARTITION <YYYYMM> FINAL` restored the exact
count. Not corruption, but readers that do NOT use FINAL will
double-count between merges: prefer a narrow `lookback_days`, and
OPTIMIZE the touched partition after a wide reconcile.

## live-lake-writer-blocks-event-loop (2026-08-06, FIXED)
`LiveLakeWriter.run_cycle` is `async def` but called blocking ClickHouse
reads and a PyIceberg **upsert** inline in the coroutine. Two compounding
defects:
1. PyIceberg has no merge-on-read ("falling back to copy-on-write"), so
   each upsert rewrote data files across the multi-million-row,
   bucket(16)-partitioned `equities.schwab_universe` — measured **~6
   minutes per 400-row chunk**, on a writer that fires every 5 minutes.
   It could never keep up.
2. Being inline, that work sat on the event loop. Observed 2026-08-06
   after restoring ~2 weeks of stream data: 7 queued chunks ≈ 40+ min
   during which EVERY endpoint hung (sectors, charts, company names,
   GEX). Looked like "the GEX page is broken"; was actually total API
   starvation.
Fix: read + write via `asyncio.to_thread`, and **append instead of
upsert** (bronze appends, silver dedups). The intentional 15-min read
window over a 5-min cycle no longer duplicates rows because an in-memory
per-provider watermark drops bars at/below the last written timestamp; a
restart may re-append at most one window, which bronze read-time dedup
absorbs.
Coverage gap that let this ship: every pre-existing test stubbed
`_read_ch` to return `[]`, so the write path had NO tests. Added
`TestWritePath` — appends-not-upserts, watermark suppression, and an
event-loop heartbeat assertion (verified to fail without the fix).
Workaround while diagnosing: `LIVE_LAKE_WRITER_ENABLED=false` (the hot
tier is unaffected — ClickHouse is what the UI reads).

## instrument-names-transient-failures-cached-as-negatives (2026-08-06, FIXED)
`_polygon_fetch` returned `None` on ANY failure — rate limit, 5xx,
timeout — and the warm path cached `None` as `{"description": ""}`. The
docstring's "cache every result including negatives, so a symbol hits
Polygon at most once" then made that permanent. Polygon's free tier
allows ~5 req/min while the warmer fetches 12-way concurrent, so a bulk
warm reliably 429s: **552 rows were cached blank**, including AMZN, AMD,
ABBV — leaving 324 stream/watchlist rows showing a bare ticker forever.
Verified Polygon resolved all of them fine on demand.
Fixes:
1. `_NameFetchUnavailable` distinguishes "provider can't answer now"
   (never cached, retried next warm) from a genuine 200-with-no-match
   (cached as a negative, as intended).
2. `bulk_refresh_from_reference()` — per-symbol lookups are the wrong
   shape for this provider. The reference endpoint paginates 1,000
   tickers/request, so the whole listed universe (13,731 names) costs
   ~14 requests instead of ~13,000, paced to stay under the limit.
Two traps inside the bulk crawl, both caught by checking row counts
rather than trusting the "written" log line:
   - `next_url` does NOT carry `limit`; without re-sending it the API
     silently drops to 100 rows/page.
   - passing `params=` to httpx REPLACES a URL's query string, dropping
     the pagination cursor — every "next" page silently re-fetched page
     1. Merge onto the URL (`httpx.URL(...).copy_merge_params`) instead.
Recovery: `DELETE FROM instrument_names WHERE name=''` then
`bulk_refresh_from_reference()`. Stream universe: 224/224 named.

**RECURRED 2026-08-07 and 2026-08-08 — the 08-06 fix was necessary but
NOT sufficient.** The nightly job (`refresh_names`, 08:00 UTC) still
called `warm(syms, refresh=True)` — 224-way concurrent per-symbol
lookups, unchanged. Under SUSTAINED load (not a one-shot burst — this
is why an ad hoc 12-symbol test didn't reproduce it), Polygon was
observed returning **HTTP 200 with an empty `results` list** for the
large majority of requests instead of 429. That is indistinguishable
from a genuine not-found in the 08-06 fix, so `_NameFetchUnavailable`
never fired and every one was cached as a confirmed negative —
**wiping 216/219 names on 08-07's run and 209/224 on 08-08's**,
including names the 08-06 fix had just repaired.
Fix (belt AND suspenders, not either/or):
1. **Invariant in `warm()`**: a "not found" answer may never downgrade
   a symbol that already has a non-empty cached name — only an actual
   resolved name may overwrite one. Closes this failure mode regardless
   of which HTTP status the provider decides to use for throttling.
2. **`refresh_names()` now calls `bulk_refresh_from_reference()`
   first** (paced, ~14 requests, complete) and only runs the per-symbol
   `warm()` on whatever the bulk crawl didn't cover — low volume, so it
   can't reproduce a 224-way hammer.
Lesson: a fix scoped to "the status codes I could reproduce in an ad
hoc test" wasn't scoped to "the failure mode." The recurrence is the
signal that the invariant belongs at the data-write layer (never erase
a known-good answer), not the fetch layer (guess which HTTP statuses
mean "not found" this week). Tests:
`test_refresh_true_never_downgrades_a_known_name_to_blank`,
`test_refresh_true_still_accepts_a_real_update`,
`test_refresh_names_uses_the_bulk_crawl_not_the_per_symbol_hammer`.
Re-repaired 2026-08-08: stream universe 224/224 named again.
