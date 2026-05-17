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

### `schwab-pricehistory-period-window-conflict`

- **Area:** provider:schwab, ingest
- **Filed:** 2026-05-15 (Phase 2 backfill)
- **Status:** open (not blocking)
- **Symptom:** Schwab's `/pricehistory` returns HTTP 400 with
  `"Enddate ... is before startDate"` when called with `period=1`
  *together* with explicit `startDate`/`endDate` on certain dates. Cost
  us all of 2026-04-03 in the Phase 2 seed-100 backfill.
- **Root cause:** Quirk in how `historical_df` in
  [app/providers/schwab_provider.py:732](../app/providers/schwab_provider.py)
  combines `period` with explicit window params. Live-streaming path is
  unaffected (uses streaming, not pricehistory).
- **Suggested fix:** Drop `period` when explicit window is passed (or
  vice versa); then re-run the Schwab bronze backfill with
  `--start 2026-04-03 --end 2026-04-03` to backfill the missing day.
  Silver dedup handles single-provider gaps gracefully, so not urgent.

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

### `watchlist-repo-containing-test-failure` — resolved 2026-05-17

`tests/test_watchlist_repo.py::test_watchlists_containing`
asserted `watchlists_containing("QQQ") == [b]` (exact equality)
but the production-seeded `default` watchlist also contains QQQ
(seeded by `migrate_default_watchlist` on app startup; CH is
shared with the running app, not an isolated test DB). Rewrote
the assertion as containment (`b in qqq_containers and a not in
qqq_containers`), matching the resilient pattern already used by
`test_list_all_active_symbols_filters_by_kind` above it.
