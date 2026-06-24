# Testing

The Pydantic contract is the test surface. If the contract is
well-tested, the impl can be swapped without breaking callers — the
whole point of the architecture.

## Layout

- `<module>/tests/` — unit tests owned by that module. They have no
  external dependencies and must pass with zero AWS / CH / provider creds.
- `tests/contract/` — in-process behavior spanning more than one module
  (for example, parity between a reader, HTTP route, and MCP tool). No live
  infrastructure.
- `tests/integration/` — live CH / PostgreSQL / S3 / provider / model tests.
  Every file is gated by the `integration` marker.
- `tests/manual/` — operator diagnostics, not pytest tests. Files here must
  not be named `test_*.py` when they are not assertion-based tests.
- `tests/support/` — fixtures or builders shared by multiple modules. Keep
  module-specific fixtures beside the module.

Put a test at the lowest level that owns all behavior under test. A service's
pure behavior belongs in that service's `tests/`; an API/service/MCP parity
test belongs in `tests/contract/`; a real database round trip belongs in
`tests/integration/`.

## Commands

```bash
poetry run pytest -m "not integration"   # unit only (default)
poetry run pytest -m integration         # live-service
```

## Key fixture

`clickhouse_ready` (session-scoped, in repository-root `conftest.py`) skips
gracefully if CH unreachable and auto-runs `init_schema()`. Any
CH-touching test depends on it. **Never** open a CH client ad-hoc.

## Async

`pytest-asyncio` configured; `async def test_*` works without per-test
decorators. If a test silently stalls, check `asyncio_mode` in
`pyproject.toml`.

## What to test (priority order)

1. **Pydantic contract** — every field, every validator.
2. **Result-object branches** — every `status="ok" | "skipped" |
   "error"`.
3. **Idempotency** — call twice; second is no-op or same state.
4. **Cross-side mutation verify** — re-read via fresh client. See
   [`coding.md`](coding.md) §5.

## Forbidden

- Asserting only "no exception raised" — that's a smoke check, not a
  test.
- Mocking the DB when the test exists to verify a write.
- New fixtures in test files when they belong in `conftest.py`.
- Skipping the `integration` marker on a live-service test — blocks CI.
