# Housekeeping review

Items discovered during the module documentation and test-layout cleanup. This
is a review queue, not authorization to delete or refactor anything.

## Awaiting owner decision

| Candidate | Evidence | Safe next decision |
|---|---|---|
| `app/services/seed/` | Its implementation identifies itself as a thin backward-compatibility facade over `StreamService`. `app/api/routes_seed.py` still imports it. | Keep until the legacy seed HTTP surface is migrated or explicitly retained; do not delete now. |
| `tests/manual/monitor_check.py` and `monitor_cli.py` | Manual scripts target unversioned `/monitors`, `/watchlist`, and `/stats`-era behavior and contain print-based exception handling rather than assertions. | Verify whether operators still use them; modernize against `/api/v1` or delete with approval. |
| `tests/manual/historical_check.py` and `livestream_check.py` | Credentialed Alpaca diagnostics, not automated tests. Equivalent provider coverage exists in module unit tests, but these scripts may retain operational value. | Confirm operational use; keep, move to an operator runbook, or delete with approval. |
| `app/data/seed_universe.py` | The stream service README marks the static seed constant for future retirement, but `app/services/universe/active_universe.py` still consumes it. | Keep until active-universe resolution reads the durable stream table. |

## Classification corrections completed

- ClickHouse-backed API, watchlist, journal, resampling, and gap-fill suites are
  now integration tests and carry the `integration` marker.
- Print-based live diagnostics are under `tests/manual/` and are not collected
  by pytest.
- Provider live checks under `scripts/` use `check_*.py` names so they are not
  mistaken for unit tests.
