# Testing

The Pydantic contract is the test surface. If the contract is
well-tested, the impl can be swapped without breaking callers — the
whole point of the architecture.

## Layout

- `tests/` — unit, no external deps. Must pass with zero AWS / CH /
  provider creds.
- `tests/integration/` — live CH / S3 / provider. Gated by
  `integration` marker.

## Commands

```bash
poetry run pytest -m "not integration"   # unit only (default)
poetry run pytest -m integration         # live-service
```

## Key fixture

`clickhouse_ready` (session-scoped, in `tests/conftest.py`) skips
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
