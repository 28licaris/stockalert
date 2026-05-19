# Testing — Markers, Fixtures, Async

Tests are the contract surface. Modules are designed liftable to
containers; the Pydantic contract is what tests should pin. If the
contract is well-tested, the implementation can be swapped without
breaking callers — which is the whole point of the architecture.

## Layout

- `tests/` — unit tests. No external deps. Must pass on a laptop with
  zero AWS / ClickHouse / provider credentials.
- `tests/integration/` — live ClickHouse / S3 / provider tests. Gated
  by the `integration` marker; skipped in default runs.

## Markers

- `@pytest.mark.integration` — required on any test touching live
  services. Default `poetry run pytest` runs unit only.

```bash
poetry run pytest -m "not integration"   # unit only (fast)
poetry run pytest -m integration         # live-service tests
```

## Fixtures

- `clickhouse_ready` (session-scoped, in `tests/conftest.py`) — skips
  gracefully if ClickHouse is unreachable and auto-runs
  `app.db.init_schema()`. Any test touching CH must depend on it. Never
  open a CH client ad-hoc inside a test.

- Shared fixtures live in `conftest.py` (root or nested), not duplicated
  per file.

## Async

`pytest-asyncio` is configured. `async def test_*` works in most files
without per-test decorators. If a test silently stalls, check
`asyncio_mode` in `pyproject.toml`.

## What to test (priority order)

1. **The Pydantic contract** (`schemas.py`) — input / output shape is
   the public boundary. Every field, every validator.
2. **Result-object branches** — every `status="ok" | "skipped" |
   "error"` path. Don't just test the happy path.
3. **Idempotency** — call twice with the same inputs, assert the second
   call is a no-op (or produces the same state). Non-negotiable for any
   ingest / backfill code.
4. **Cross-side mutation verification** — after a write, re-read via a
   *new* client / catalog and assert (snapshot changed, rows present).
   See [`coding.md`](coding.md) rule 5.

## Anti-patterns

- A test that asserts only "no exception raised". That's not a test;
  it's a smoke check. Assert on returned state.
- Mocking the database when the test exists to verify a write.
  Integration tests must hit a real ClickHouse / real Iceberg.
- New fixtures in individual test files when they would belong in
  `conftest.py`.
- Skipping the `integration` marker because "it works locally for me".
  CI runs default `pytest`; unmarked tests block the build.

## Related

- [`service_modules.md`](service_modules.md) — what makes the contract
  testable in isolation.
- [`coding.md`](coding.md) — cross-side mutation verification, the
  "no silent failures" foundation.
