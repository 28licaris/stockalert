# Housekeeping review

Items discovered during the module documentation and test-layout cleanup. This
is a review queue, not authorization to delete or refactor anything.

## Awaiting owner decision

| Candidate | Evidence | Safe next decision |
|---|---|---|
| `tests/manual/monitor_check.py` and `monitor_cli.py` | Manual scripts target unversioned `/monitors`, `/watchlist`, and `/stats`-era behavior and contain print-based exception handling rather than assertions. | Verify whether operators still use them; modernize against `/api/v1` or delete with approval. |
| `tests/manual/historical_check.py` and `livestream_check.py` | Credentialed Alpaca diagnostics, not automated tests. Equivalent provider coverage exists in module unit tests, but these scripts may retain operational value. | Confirm operational use; keep, move to an operator runbook, or delete with approval. |

## Classification corrections completed

- ClickHouse-backed API, watchlist, journal, resampling, and gap-fill suites are
  now integration tests and carry the `integration` marker.
- Print-based live diagnostics are under `tests/manual/` and are not collected
  by pytest.
- Provider live checks under `scripts/` use `check_*.py` names so they are not
  mistaken for unit tests.
- The legacy seed service/API and static equity seed universe were retired;
  ClickHouse `stream_universe` is now the fail-loud sole runtime authority.
