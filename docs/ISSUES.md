# StockAlert — Issue Tracker

In-repo issue list for bugs, flaky tests, blockers, and follow-up
cleanup. Lighter than GitHub Issues; lives in-repo so context goes
with the code and PRs can cite IDs.

For longer-running work (phases, multi-step initiatives) use
[BUILD_JOURNAL.md](BUILD_JOURNAL.md) instead. This file is for the
"things that are wrong" list.

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
fine, we'd just like more of it." Backlog goes in
[BUILD_JOURNAL.md](BUILD_JOURNAL.md).

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

### `cockpit-chart-time-axis-broken`

- **Area:** ui
- **Filed:** 2026-05-19
- **Status:** open
- **Symptom:** Switching candle intervals (1m / 5m / 15m / 1h / 1d) on
  `/app/symbol/<ticker>` makes the time-axis labels look wrong —
  dates collapse, intraday timestamps appear on daily charts, or the
  axis density doesn't match the interval. Operator's read:
  "the time on X axis is all messed up."
- **Root cause:** likely a combination of (a) `OhlcvChart.tsx` passes
  Unix-epoch seconds via `toUnix()` for every interval, including
  daily where Lightweight Charts expects YYYY-MM-DD strings;
  (b) no `tickMarkFormatter` configured per interval; (c)
  `timeVisible: true, secondsVisible: false` is set globally which
  is wrong for the `1d` case.
- **Suggested fix:**
  1. In `OhlcvChart.tsx`, branch on interval at chart-create time —
     daily/4h use `BusinessDay`-style time values (YYYY-MM-DD);
     intraday uses `UTCTimestamp`. LWC accepts both per series, but
     mixing them within one series breaks the axis.
  2. Add a `tickMarkFormatter` that renders dates for `1d`/`4h`,
     `HH:mm` for `1m`/`5m`/`15m`/`30m`, and `MMM dd HH:mm` for `1h`.
  3. Set `timeVisible` + `secondsVisible` from a per-interval table.
  4. After fix: switching 1m → 1d on AAPL should show distinct date
     labels (e.g. "May 18", "May 15") not Unix offsets.

### `cockpit-bars-table-overnight-gaps`

- **Area:** ui
- **Filed:** 2026-05-19
- **Status:** open
- **Symptom:** The "Recent bars" table beneath the chart shows
  apparent 30-minute (and larger) gaps. The gaps are real — they
  correspond to after-hours close → next pre-market open — but the
  cockpit surfaces them as if data is missing rather than as session
  boundaries.
- **Root cause:** `BarsTable.tsx` renders the raw `bars[]` array
  ordered by timestamp. After 20:00 ET (post-market close) there are
  no bars until 04:00 ET the next morning, so rows jump 8 hours.
  TradingView solves this by collapsing non-session time entirely
  on the X-axis ("trading hours only" mode); the bars table needs
  the same option.
- **Suggested fix:**
  1. Add a "Session hours only" toggle to the bars table header
     (default ON for intraday intervals, OFF for daily).
  2. When ON, filter rows to regular-session bars (09:30–16:00 ET).
     Use the same ET-trading-day boundary helper documented in
     `docs/standards/data/timezone_et_vs_utc.md` (memory pointer).
  3. When OFF, render the raw stream but add a subtle visual divider
     between sessions (a single thin row showing
     "— after hours close →") so the gap isn't mistaken for missing
     data.
  4. Apply the same filter logic to the chart itself per
     `cockpit-chart-time-axis-broken`'s "session-collapse" follow-on.

### `cockpit-chart-indicator-overlays`

- **Area:** ui
- **Filed:** 2026-05-19
- **Status:** open
- **Symptom:** No way to overlay SMA, EMA, RSI, MACD, Bollinger, ATR
  on the symbol chart. Indicator pane on legacy `/symbol/<ticker>`
  static dashboard had this; cockpit equivalent doesn't.
- **Root cause:** Backend support exists and is typed:
  `GET /api/v1/indicators/series` and
  `POST /api/v1/indicators/chart-data` (already typed in
  FE-CONTRACTS-2 era). No frontend consumer — the `OhlcvChart`
  component doesn't accept overlay props; the Symbol page has no
  indicator picker.
- **Suggested fix:**
  1. Add an "Indicators" button + popover to the Symbol page header
     (next to the interval picker). Two sections:
     - **Active indicators** — list of currently displayed
       indicators with edit-in-place params, color swatch, and
       remove button. Each row is a `(indicator, params, color)`
       triple.
     - **Add indicator** — combo picker (search the registry by
       name) → opens an inline form for params + initial color,
       then "Add."
  2. Each active indicator fires a `useIndicatorSeries` hook keyed
     by (symbol, interval, indicator, params).
  3. Extend `OhlcvChart` with an `overlays` prop accepting an array
     of `{indicator, params, color, series}` items:
     - **Price-pane overlays** (SMA, EMA, Bollinger, VWAP) — line
       series on the same pane as the candles.
     - **Separate-pane overlays** (RSI, MACD, ATR) — a new pane
       below the volume pane, sized to ~120 px.
  4. **Color picker per indicator.** Each indicator row in the
     popover has a circular color swatch. Click → opens a popover
     with the platform's named palette (up, down, accent, +
     8 distinct trading-friendly colors) AND a freeform hex input.
     Defaults rotate through a curated palette but the operator can
     override.
  5. **Persistence — the "last added" semantic.** Selection is
     stored via `useUserSetting('chart.indicators', [...])`. Adding
     SMA(20) blue + RSI(14) amber persists; on next chart visit
     (same OR different symbol) the same indicators reappear with
     the same params + colors. Per-symbol overrides are a follow-on;
     today's model is "global default chart layout."

### `cockpit-chart-default-preferences`

- **Area:** ui
- **Filed:** 2026-05-19
- **Status:** open
- **Symptom:** "Configurable like any trading platform" — operator
  expects the chart to remember their last interval, last range,
  and last indicator set across reloads + across different symbols.
  Today the Symbol page hard-codes `DEFAULT_INTERVAL = "5m"` and
  has no range picker at all.
- **Root cause:** Symbol page constants instead of operator
  preferences. The `useUserSetting` seam is already in place
  ([frontend/src/lib/storage.ts]) so this is a wiring change, not
  new infrastructure.
- **Suggested fix:**
  1. Replace the hard-coded `DEFAULT_INTERVAL` with:
     `const [interval, setInterval] = useUserSetting<Interval>('chart.defaultInterval', '5m');`
     Same hook for `chart.defaultRange` once the range selector
     lands (pairs with `cockpit-chart-time-range-selector`).
  2. Every change to the picker writes the new value through, so
     the operator's most-recently-used interval is the default on
     their next visit.
  3. **Reset to defaults** affordance — a small "↺" button beside
     the interval picker resets all chart-related preferences to
     the platform defaults (5m interval, 1M range, no indicators).
     One-click escape hatch.
  4. Sync across tabs of the same browser via the localStorage
     `storage` event so opening AAPL in one tab and changing the
     interval doesn't leave another tab's NVDA out of sync.
  5. **Future:** when SaaS lands, the same `useUserSetting` keys
     migrate to per-tenant `/api/v1/me/prefs` automatically — no
     component changes needed (the seam already abstracts this).
     Per [frontend_api_contracts.md §7.3].

### `cockpit-chart-time-range-selector`

- **Area:** ui
- **Filed:** 2026-05-19
- **Status:** open
- **Symptom:** Cannot pick an X-axis time range like ThinkOrSwim or
  TradingView (1D · 5D · 1M · 3M · 6M · YTD · 1Y · 2Y · ALL).
  Currently only the candle interval is configurable; the chart
  always fetches `limit=500` regardless of how far back the operator
  wants to look.
- **Root cause:** `symbol.tsx` only has the interval picker. The
  `/api/v1/bars` endpoint already accepts `lookback_days` (server-
  side window) but no UI sends it.
- **Suggested fix:**
  1. Add a time-range chip row to the Symbol page header (right of
     the interval picker): `1D · 5D · 1M · 3M · 6M · YTD · 1Y · 2Y · ALL`.
  2. Map each chip to a `lookback_days` value:
     - 1D=1, 5D=5, 1M=30, 3M=90, 6M=180, YTD=current-day-of-year,
       1Y=365, 2Y=730, ALL=null (omit param).
  3. `useSymbolBars(symbol, interval, lookbackDays, limit)` extends
     its signature; the hook computes a sensible `limit` from
     interval × lookback to stay under the 100k server cap.
  4. Some interval × range combinations are nonsensical (1m bars
     over 2Y = ~750k bars). The chip row disables incompatible
     combinations and shows a tooltip explaining why.
  5. Persist selection via `useUserSetting('symbol.range', '1M')`.
  6. Pair with `cockpit-chart-time-axis-broken` — the tick formatter
     also adapts to the selected range, not just the interval.

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

### `schwab-chart-fields-test-drift` — resolved 2026-05-17

Three `TestChartContentToBar` / `TestDataProviderContract` fixtures
in `tests/test_schwab_provider.py` were rewritten to use the
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
- RSI: `tests/test_indicators_ta3.py` + the screener, MTF, and
  MCP-live test suites.
- Divergence: now at `app/signals/divergence.py`; covered by
  `tests/test_monitors_manual.py`, `tests/test_mcp_live.py`,
  `tests/test_readers_unit.py`.
- Production websocket (`/ws/signals` on `app/main_api.py`):
  currently uncovered. Filed as separate follow-up — not the
  same thing the deleted scaffold was attempting.

### `schwab-pricehistory-period-window-conflict` — resolved 2026-05-17

Two findings, both addressed:

**1. Code fix (correctness).** `historical_df` in
[app/providers/schwab_provider.py](../app/providers/schwab_provider.py)
used to send `period` alongside explicit `startDate`/`endDate` for
`periodType=day` (minute charts). Per
[docs/schwab-api/market_data_api.md](schwab-api/market_data_api.md):
*"If not specified startDate will be (endDate - period) ..."* —
`period` is purely a default-startDate derivation, ignored when
`startDate` is explicit. Sending both can confuse Schwab's internal
date math. Dropped `period` entirely from the request payload;
pinned the new behavior with
`tests/test_schwab_provider.py::TestHistoricalDf::test_single_day_window_does_not_send_period`
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

`tests/test_watchlist_repo.py::test_watchlists_containing`
asserted `watchlists_containing("QQQ") == [b]` (exact equality)
but the production-seeded `default` watchlist also contains QQQ
(seeded by `migrate_default_watchlist` on app startup; CH is
shared with the running app, not an isolated test DB). Rewrote
the assertion as containment (`b in qqq_containers and a not in
qqq_containers`), matching the resilient pattern already used by
`test_list_all_active_symbols_filters_by_kind` above it.
