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
| Lake / ML architecture (canonical v2) | [`docs/architecture_v2/`](docs/architecture_v2/README.md) (**source of truth for lake design**) |
| Adding a symbol / ingest paths | [`data/symbol_lifecycle.md`](docs/standards/data/symbol_lifecycle.md) |
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
docker compose --profile identity up -d postgres  # local identity PostgreSQL
docker compose --profile full up --build          # full stack
```

## Layer map (v2 — post Phase 1 migration)

```
app/api/             FastAPI routes (routes_*.py — one per domain)
app/services/        Domain modules (see standards/service_modules.md)
  identity/          Customer users/tenants/sessions; Pydantic contracts + PostgreSQL repository;
                     tenant-scoped, CSRF-protected session management
  equities/          v2 lake: schemas + sink + tables + gaps + models
  futures/           v2 futures mirror: schemas/sink/tables/gaps +
                     universe + symbols (/-prefix routing) + lake_to_ch_fill
  ingest/            Provider → lake writers (Polygon flat-files,
                     Schwab REST/live, corp-actions, nightly_futures_refresh)
  readers/           Lake → consumer Pydantic shapes
                     (AdjustedOhlcvReader, BronzeReader, CorpActionsReader);
                     bars_gateway routes /-prefix symbols to futures tables
  live/              Live-tier orchestration (stream, watchlist)
  sim/               Backtest (see standards/trading_subsystem.md)
  journal/  screener/  universe/  legacy/
app/db/              ClickHouse client + schemas; PostgreSQL engine wiring
app/providers/       base.py + alpaca/polygon/schwab
app/indicators/      Pure math, registry pattern
app/signals/         Pattern detectors (pure fns)
app/mcp/             MCP server + tools (agent entry)
app/config.py        Settings (Pydantic) — single env-var source
scripts/             Ops scripts:
                       lake_import_athena.py — one-time bulk-load
                         from existing S3 cache → equities.polygon_raw
                       polygon_history_backfill.py — reusable Polygon
                         flat-files puller into equities.polygon_raw
                       schwab_history_backfill.py — Schwab REST →
                         equities.schwab_universe
                       futures_history_backfill.py — Schwab REST →
                         futures.schwab_futures (continuous roots)
                       run_corp_actions_backfill.py — Polygon REST →
                         equities.market_corp_actions
                       spark/polygon_adjustment_job.py — whole-market
                         weekly Spark job → equities.polygon_adjusted
                       rebuild_ch_from_silver.py — bulk lake → CH
                         (legacy filename, sources from v2 lake)
docs/                Plans, runbooks; docs/standards/ = rules
tests/  tests/integration/
```

Lake tables (v2 / `equities.*` Glue DB):

| Table | Source | Notes |
|---|---|---|
| `equities.polygon_raw` | Polygon flat-files (CV7 nightly, CV3 history puller) | Raw unadjusted, bucket(32, symbol) + month(timestamp) |
| `equities.polygon_adjusted` | Spark job (CV5) reads raw + corp_actions | Split-adjusted + `adj_factor` column; merge-on-read |
| `equities.schwab_universe` | Schwab live + REST (CV8) | Pre-adjusted (adj_factor=1.0); bucket(16) |
| `equities.market_corp_actions` | Polygon REST (CV9) | Splits + dividends; month(ex_date) partition |

Futures lake (separate `futures.*` Glue DB + `iceberg/futures/` S3):

| Table | Source | Notes |
|---|---|---|
| `futures.schwab_futures` | Schwab live (F2) + REST nightly (F3) | Continuous roots (/ES,…); no adjustment tier; month(timestamp) partition |

## Docs

| Doc | When |
|-----|------|
| [`docs/standards/`](docs/standards/README.md) | **Always — the rules** |
| [`docs/architecture_v2/`](docs/architecture_v2/README.md) | v2 lake / ML design (canonical) |
| `docs/ARCHITECTURE.md` | Redirect stub — points to the canonical docs after v1 deprecation |
| `docs/ISSUES.md` | Known issues, flaky tests |
| `docs/COMMANDS.md` | Cheatsheet |
| `docs/architecture_v2/07_runbook.md` | Operator procedures |
| `docs/trading_subsystem_design.md` | Trading subsystem contract |

Plan vs code conflict: code wins. The journal lives in detailed
commit messages; BUILD_JOURNAL was retired 2026-05-18 and the file
deleted 2026-05-21 (CV18).

## Infra

AWS only. Glue + S3 (Iceberg warehouse), CodeBuild for long backfills
(`scripts/codebuild/`), EMR Serverless for the weekly Spark adjustment
job. No CDK / Terraform. Self-hosted ClickHouse.
Secrets via `.env` (gitignored; template in `.env.example`, docs in
`CONFIG.md`).
