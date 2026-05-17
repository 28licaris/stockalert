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

### `schwab-chart-fields-test-drift`

- **Area:** tests, provider:schwab
- **Filed:** (pre-existing)
- **Status:** open
- **Symptom:** Three tests in
  [tests/test_schwab_provider.py](../tests/test_schwab_provider.py) fail
  with values shifted by one field:
  - `TestChartContentToBar::test_maps_key_and_numeric_fields`
  - `TestChartContentToBar::test_maps_string_keys`
  - `TestDataProviderContract::test_bar_has_required_attributes`
- **Root cause:** Test fixtures use the *documented* Schwab CHART_EQUITY
  field map (`1=Open, 2=High, …`). The implementation in
  [app/providers/schwab_provider.py](../app/providers/schwab_provider.py)
  intentionally uses the empirically-validated map
  (`2=Open, 3=High, 4=Low, 5=Close, 6=Volume, 7=ChartTime(ms)`;
  `CHART_EQUITY_FIELDS = "0,2,3,4,5,6,7"`) because Schwab's published
  field table is wrong. Tests were never updated to match the live
  behavior.
- **Suggested fix:** Update the three test fixtures to use field IDs
  `2..7` (and add a comment pointing back to the production constant).
  Don't change the implementation — it's correct.

### `schwab-streamer-url-key-test-drift`

- **Area:** tests, provider:schwab
- **Filed:** (pre-existing)
- **Status:** open
- **Symptom:** Two tests in
  [tests/test_schwab_provider.py](../tests/test_schwab_provider.py) fail
  asserting `_streamer_url` was discovered:
  - `TestGetUserPrincipals::test_sets_streamer_url_from_list`
  - `TestGetUserPrincipals::test_sets_streamer_url_from_dict_nested`
- **Root cause:** Tests build mock payloads using the legacy
  `streamerConnectionInfo` key (TD-Ameritrade era). Schwab's current
  Trader API returns the connection details under `streamerInfo[]`, and
  the production parser only reads from there (see
  `_get_user_principals` in
  [app/providers/schwab_provider.py](../app/providers/schwab_provider.py)).
- **Suggested fix:** Update the test payloads to use `streamerInfo` with
  `streamerSocketUrl`. The newer
  `TestGetUserPrincipals::test_sets_streamer_url_from_streamer_info`
  already covers the production-shape happy path, so the two
  legacy-shape tests should either be rewritten against `streamerInfo`
  or deleted as superseded.

### `pre-existing-test-collection-errors`

- **Area:** tests
- **Filed:** 2026-05-14 (called out during Phase 0 gate)
- **Status:** open
- **Symptom:** Three test files fail to collect under pytest:
  [tests/test_alert_flow.py](../tests/test_alert_flow.py),
  [tests/test_indicators.py](../tests/test_indicators.py),
  [tests/test_websocket.py](../tests/test_websocket.py).
- **Root cause:** They reference modules that have never existed in the
  repo (`app.services.alert_service`, etc.). Errors date back to the
  initial commit `ab6e71d`.
- **Suggested fix:** Either rewrite each against the modules that
  actually exist (`app/indicators/`, `app/signals/`,
  `app/services/live/monitor_service.py`) or delete them. The
  `test_indicators.py` case imports `app.indicators.divergence` which is
  the old pre-`signals/`-split path; the rewritten target is
  `app.signals.divergence`.

### `watchlist-repo-containing-test-failure`

- **Area:** tests, db
- **Filed:** 2026-05-14 (called out during Phase 0 gate)
- **Status:** open
- **Symptom:**
  `tests/test_watchlist_repo.py::test_watchlists_containing` fails.
  One failure, not yet diagnosed.
- **Root cause:** unknown — needs investigation.
- **Suggested fix:** unknown.

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
