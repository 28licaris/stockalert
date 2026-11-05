# Testing

The Pydantic contract is the test surface. If the contract is
well-tested, the impl can be swapped without breaking callers — the
whole point of the architecture.

## Layout

- `<module>/tests/` — unit tests owned by that module. They have no
  external dependencies and must pass with zero AWS / CH / provider creds —
  **and identically whether or not those creds happen to be present.**
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

## CI

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs on every
pull request into `main` and on every push to `main`. Three jobs, in
parallel; all three must be green before a merge:

| Job | What runs | What it proves |
|---|---|---|
| `backend` | Python 3.12, Poetry venv from `poetry.lock`, `pytest -m "not integration" -q` | Every unit + contract test passes with **no** credentials and no live service |
| `frontend` | Node 20, `npm ci`, `npm run typecheck`, `npm run lint` | The cockpit compiles and is lint-clean at zero warnings |
| `image` | `docker buildx build --platform linux/arm64` of the repo `Dockerfile`, no push | The deployable image still builds |

**Integration tests never run in CI.** `-m integration` is a manual run on
an operator's machine with live ClickHouse / PostgreSQL / S3 / provider
credentials (`poetry run pytest -m integration`). That is why the
`integration` marker is mandatory on every live-service test: an unmarked
one turns the `backend` job red for everyone.

A new push to the same PR cancels the run in flight; runs on `main` are
never cancelled. Frontend `codegen` is not run in CI (it needs a live API);
`tests/contract/test_openapi_types_in_sync.py` is what catches a stale
`types.gen.ts` instead.

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

## Inject every collaborator, not just the one under test

A service that lazily builds its own client (`X.from_settings()` when none was
passed) will reach the network in a unit test unless **every** such
collaborator is injected. When construction is gated on an env var, the test
then passes on a machine without that key and fails on one with it — so it
looks flaky or "pre-existing" rather than wrong.

`EconService` is the worked example: its two ingest tests stubbed the BLS
client but not BEA, so on any machine with `BEA_API_KEY` set they made live
calls to `apps.bea.gov`, returned real GDP/PCE data, and failed their
row-count assertions. They were wrong for months and read as a known failure.
Runtime is the tell — those tests dropped from 3.5s to 0.07s once BEA was
injected.

**If a test's runtime is dominated by anything other than CPU, it is talking to
something it should not be.**

## Forbidden

- Asserting only "no exception raised" — that's a smoke check, not a
  test.
- Mocking the DB when the test exists to verify a write.
- New fixtures in test files when they belong in `conftest.py`.
- Skipping the `integration` marker on a live-service test — blocks CI.
- Leaving a collaborator un-injected in a unit test, so the real client is
  constructed when an env var happens to be set.
