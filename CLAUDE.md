# StockAlert — Repo Guide

Modular monolith → microservice platform for **AI/ML-driven equities
day/swing trading**.

## Standards (read first — non-negotiable)

| When | Read |
|------|------|
| Any code change | [`engagement.md`](docs/standards/engagement.md) (spec-first), [`coding.md`](docs/standards/coding.md) (no silent failures) |
| Architectural call | [`platform_design.md`](docs/standards/platform_design.md) |
| Editing `app/services/*` | [`service_modules.md`](docs/standards/service_modules.md) |
| Tests | [`testing.md`](docs/standards/testing.md) |
| New service / arch change | [`doc_discipline.md`](docs/standards/doc_discipline.md) |
| `sim/` / strategies / indicators | [`trading_subsystem.md`](docs/standards/trading_subsystem.md) |
| Bronze sinks / maintenance | [`data/bronze_idempotency.md`](docs/standards/data/bronze_idempotency.md) |
| Silver schema | [`data/lean_silver.md`](docs/standards/data/lean_silver.md) |
| Adding a symbol / ingest paths | [`data/symbol_lifecycle.md`](docs/standards/data/symbol_lifecycle.md) (v1 locked) |
| Lake / ML architecture (proposed v2) | [`docs/architecture_v2/`](docs/architecture_v2/README.md) (**read before lake/ingest refactors**) |
| Trading-day math | [`data/timezone_et_vs_utc.md`](docs/standards/data/timezone_et_vs_utc.md) |
| Athena SQL | [`data/athena_dialects.md`](docs/standards/data/athena_dialects.md) |

**Engagement:** no code without an approved spec. Restate, confirm,
write.

## Stack

Python 3.12 · Poetry · FastAPI + uvicorn
Hot: ClickHouse (live/recent). Cold: Iceberg on S3 + Glue (history, ML).
Providers (env-switched): Alpaca, Polygon, Schwab. Agent surface: MCP.

## Commands

```bash
poetry install
poetry run uvicorn app.main_api:app --reload      # API on :8000
poetry run pytest                                 # all tests
poetry run pytest -m "not integration"            # unit only (fast)
poetry run pytest -m integration                  # live-service
poetry run pytest tests/test_foo.py::test_bar     # single test
docker compose --profile ch up -d                 # local ClickHouse
docker compose --profile full up --build          # full stack
```

## Layer map

```
app/api/             FastAPI routes (routes_*.py — one per domain)
app/services/        Domain modules (see standards/service_modules.md)
  bronze/  silver/  ingest/  readers/  live/
  sim/                Backtest (see standards/trading_subsystem.md)
  journal/  screener/  universe/  legacy/
app/db/              ClickHouse client + schemas
app/providers/       base.py + alpaca/polygon/schwab
app/indicators/      Pure math, registry pattern
app/signals/         Pattern detectors (pure fns)
app/mcp/             MCP server + tools (agent entry)
app/config.py        Settings (Pydantic) — single env-var source
scripts/             Ops (backfill, audit, verify, codebuild/)
docs/                Plans, runbooks; docs/standards/ = rules
tests/  tests/integration/
```

## Docs

| Doc | When |
|-----|------|
| [`docs/standards/`](docs/standards/README.md) | **Always — the rules** |
| `docs/ARCHITECTURE.md` | System overview, service map |
| `docs/ISSUES.md` | Known issues, flaky tests |
| `docs/COMMANDS.md` | Cheatsheet |
| `docs/data_platform_plan.md` | Lake / Iceberg / medallion roadmap |
| `docs/silver_layer_plan.md` | Silver build details |
| `docs/trading_subsystem_design.md` | Trading subsystem contract |
| `docs/runbook_*.md` | Operator procedures |

Plan vs code conflict: code wins. (BUILD_JOURNAL retired 2026-05-18 —
detailed commit messages are the journal.)

## Infra

AWS only. Glue + S3 (Iceberg warehouse), CodeBuild for long backfills
(`scripts/codebuild/`). No CDK / Terraform. Self-hosted ClickHouse.
Secrets via `.env` (gitignored; template in `.env.example`, docs in
`CONFIG.md`).
