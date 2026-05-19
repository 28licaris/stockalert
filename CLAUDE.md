# StockAlert — Repo Guide for Claude

Modular monolith on a deliberate path to a microservice platform for
**AI/ML-driven equities day/swing trading**. Auto-memory `MEMORY.md`
carries the principles; this file is the repo-specific cheat sheet.

## Engagement rule (read first)

**No code without an approved requirement or spec.** Restate the ask,
confirm scope, then write. Plans in `docs/` are guidance, not
pre-authorization. Details: `feedback_spec_first.md` in auto-memory.

## Stack

Python 3.12 · Poetry · FastAPI + uvicorn
Hot tier: ClickHouse (live/recent)
Cold tier: Apache Iceberg on S3 + AWS Glue catalog (history, ML training)
Providers: Alpaca, Polygon, Schwab (selected via env: `DATA_PROVIDER`,
`STREAM_PROVIDER`, `HISTORY_PROVIDER`)
Agent surface: MCP server (`app/mcp/`)

## Commands

```
poetry install                                    # install deps
poetry run uvicorn app.main_api:app --reload      # run API locally :8000
poetry run pytest                                 # all tests
poetry run pytest -m "not integration"            # unit only (fast)
poetry run pytest -m integration                  # live-service tests
poetry run pytest tests/test_foo.py::test_bar     # single test
docker compose --profile ch up -d                 # local ClickHouse
docker compose --profile full up --build          # full stack
```

Lint/type tooling is not enforced in `pyproject.toml` — confirm with the
user before adding a tool. Don't introduce ruff/mypy without signoff.

## Layer map

```
app/api/             FastAPI routes (routes_*.py — one per domain)
app/services/        Domain modules (see feedback_service_module_design)
  bronze/            Raw ingest — append-only, per-(provider, kind) tables
  silver/            Canonical OHLCV + corp_actions (deduped, adjusted)
  ingest/            Backfill workers (polygon flatfiles, schwab, corp_actions)
  readers/           Query wrappers — what agents/MCP read through
  live/              Real-time watchlist + signal streaming
  sim/               Backtest engine (see feedback_trading_subsystem_design)
  journal/  screener/  universe/  legacy/
app/db/              ClickHouse client + schemas
app/providers/       base.py + alpaca/polygon/schwab subclasses
app/indicators/      Pure math, registry pattern (RSI/MACD/TSI/EMA/...)
app/signals/         Pattern detectors (divergence, breakouts) — pure fns
app/mcp/             MCP server + tools (agent entry point)
app/config.py        Settings (Pydantic) — single source for env vars
scripts/             Ops scripts (backfill, audit, verify, codebuild/)
docs/                Plans, journal, runbooks (see below)
tests/               Pytest tree; tests/integration/ for live-service tests
```

## Docs to consult before non-trivial work

| Doc                                | When                                          |
|------------------------------------|-----------------------------------------------|
| `docs/BUILD_JOURNAL.md`            | Ground truth — phases, gates, decisions       |
| `docs/ARCHITECTURE.md`             | System overview, service map                  |
| `docs/coding_standards.md`         | Authoritative house rules                     |
| `docs/ISSUES.md`                   | Known issues, flaky tests                     |
| `docs/COMMANDS.md`                 | Copy-paste cheatsheet                         |
| `docs/data_platform_plan.md`       | S3 lake / Iceberg / medallion roadmap         |
| `docs/silver_layer_plan.md`        | Silver build details                          |
| `docs/runbook_*.md`                | Operator procedures (silver build, etc.)      |

When journal and plan disagree, journal wins.

## Infra

AWS only. Glue catalog + S3 (Iceberg warehouse), CodeBuild for long
backfills (`scripts/codebuild/buildspec.yml`,
`scripts/codebuild/run_silver_build.sh`,
`scripts/codebuild/post_build.sh`). No CDK/Terraform — IAM/build config
lives in the repo as JSON/YAML. Self-hosted ClickHouse. Secrets via
`.env` (gitignored — template in `.env.example`, vars documented in
`CONFIG.md`).

## Conventions (enforced by auto-memory)

- **Config:** `from app.config import settings` — never bare
  `os.environ.get`. New service receives deps via constructor;
  `from_settings()` is the production-construction factory.
- **Logger:** `logger = logging.getLogger(__name__)` per module. Log
  every outcome including zero/empty — see `feedback_coding_standards`.
- **Startup:** wrap non-foundation tasks in `_safe_start()`
  (`app/main_api.py`) so one subsystem's failure doesn't poison others.
- **New service:** follow `feedback_service_module_design` template
  (schemas/contract/service/tests/README). README ships in the same
  commit as the code.
- **Cross-service imports:** `schemas.py` or `contract.py` only —
  never `service.py`. This is what keeps services liftable to containers.
- **Writes:** verify cross-side. After Iceberg write, reload via a fresh
  catalog and assert snapshot changed. "No exception" ≠ "data written."
  See `feedback_coding_standards` rule 5.

## When in doubt

Three test questions for any architectural call (from
`feedback_platform_design_intent`):
1. **Lift-out:** can this still work as its own container next month?
2. **Agent:** can an LLM agent use this without bespoke integration?
3. **Replay:** can a training run six months out reproduce it bit-for-bit?

If any answer is no, the design is incomplete — surface it before coding.
