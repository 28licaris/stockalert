# StockAlert — Known Issues & Backlog

Living document for bugs, flaky tests, and follow-up cleanup. Add new items at
the top of `## Open`. Move resolved items to `## Resolved` with a date so we
keep a short history without losing context.

Each entry should have:

- **ID** so we can reference it in PRs / commits
- **Area** so it's easy to filter (provider, db, ui, infra, tests, ...)
- **Symptom** — what the user / CI actually sees
- **Root cause** — best current understanding
- **Suggested fix** — leave empty if unknown
- **Status** — `open`, `in-progress`, `blocked`, `wontfix`, `resolved`

---

## Open

### `schwab-chart-fields-test-drift`

- **Area:** tests, provider:schwab
- **Symptom:** Three tests in [stockalert/tests/test_schwab_provider.py](tests/test_schwab_provider.py) fail with values shifted by one field:
  - `TestChartContentToBar::test_maps_key_and_numeric_fields`
  - `TestChartContentToBar::test_maps_string_keys`
  - `TestDataProviderContract::test_bar_has_required_attributes`
- **Root cause:** Test fixtures use the *documented* Schwab CHART_EQUITY field map
  (`1=Open, 2=High, …`). The implementation in
  [stockalert/app/providers/schwab_provider.py](app/providers/schwab_provider.py)
  intentionally uses the empirically-validated map
  (`2=Open, 3=High, 4=Low, 5=Close, 6=Volume, 7=ChartTime(ms)`; `CHART_EQUITY_FIELDS = "0,2,3,4,5,6,7"`)
  because Schwab's published field table is wrong. Tests were never updated to
  match the live behaviour.
- **Suggested fix:** Update the three test fixtures to use field IDs `2..7` (and
  add a comment pointing back to the production constant). Don't change the
  implementation — it's correct.
- **Status:** open

### `schwab-streamer-url-key-test-drift`

- **Area:** tests, provider:schwab
- **Symptom:** Two tests in
  [stockalert/tests/test_schwab_provider.py](tests/test_schwab_provider.py) fail
  asserting `_streamer_url` was discovered:
  - `TestGetUserPrincipals::test_sets_streamer_url_from_list`
  - `TestGetUserPrincipals::test_sets_streamer_url_from_dict_nested`
- **Root cause:** Tests build mock payloads using the legacy
  `streamerConnectionInfo` key (TD-Ameritrade-era). Schwab's current Trader API
  returns the connection details under `streamerInfo[]`, and the production
  parser only reads from there (see `_get_user_principals` in
  [stockalert/app/providers/schwab_provider.py](app/providers/schwab_provider.py)).
- **Suggested fix:** Update the test payloads to use `streamerInfo` with
  `streamerSocketUrl`. The newer
  `TestGetUserPrincipals::test_sets_streamer_url_from_streamer_info` already
  covers the production-shape happy path, so the two legacy-shape tests should
  either be rewritten against `streamerInfo` or deleted as superseded.
- **Status:** open

---

## Resolved

<!-- format:
### `<id>` — resolved YYYY-MM-DD
brief summary + commit / PR link
-->
