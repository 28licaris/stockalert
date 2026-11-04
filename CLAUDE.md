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
| Merging any branch | [`merge_discipline.md`](docs/standards/merge_discipline.md) (rebase first, verify the feature after) |
| `sim/` / strategies / indicators | [`trading_subsystem.md`](docs/standards/trading_subsystem.md) |
| Any architecture / data-platform question | [`docs/architecture/`](docs/architecture/README.md) (**the source of truth**) |
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
poetry run pytest app/providers/tests/test_schwab_provider.py::TestChartContentToBar  # single test
docker compose --profile ch up -d                 # local ClickHouse
docker compose --profile identity up -d postgres  # local identity PostgreSQL
docker compose --profile full up --build          # full stack
```

## Layer map

```
app/api/             FastAPI routes (routes_*.py — one per domain)
app/services/        Domain modules (see standards/service_modules.md)
  identity/          Customer users/tenants/sessions; Pydantic contracts + PostgreSQL repository;
                     tenant-scoped session management + security audit trail
  equities/          Equities lake: schemas + sink + tables + gaps + models
  futures/           Futures mirror: schemas/sink/tables/gaps +
                     universe + symbols (/-prefix routing) + lake_to_ch_fill
  options/           Options lake: Schwab chain schemas/parser + tables/sink;
                     derived GEX snapshots (MCP/API readers follow)
  ingest/            Provider → lake writers (Polygon flat-files,
                     Schwab REST/live, corp-actions, nightly_futures_refresh)
  readers/           Lake → consumer Pydantic shapes
                     (AdjustedOhlcvReader, BronzeReader, CorpActionsReader);
                     bars_gateway routes /-prefix symbols to futures tables
  live/              Live-tier orchestration (stream, watchlist)
  sim/               Backtest (see standards/trading_subsystem.md)
  ppl/               Posterior predictive levels: GJR-GARCH(1,1) + filtered
                     historical simulation → forward price fan + quantile
                     levels. Gate is PIT calibration, not MCPT (it makes no
                     directional claim). See docs/trading/ppl_mathematics.md
  vol_premium/       Ranks the options universe by implied vol vs the PPL
                     forecast ("where are options expensive relative to how
                     much the underlying actually moves"). ThetaData EOD
                     history → options_atm_iv_daily is the baseline
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
                       rebuild_ch_from_lake.py — bulk lake → CH
                         (canonical; per-symbol via fill_ch_from_lake /
                          read_arrow union, schwab tip included)
docs/                Plans, runbooks; docs/standards/ = rules
tests/  tests/integration/
```

Lake tables (`equities.*` Glue DB):

| Table | Source | Notes |
|---|---|---|
| `equities.polygon_raw` | Polygon flat-files | Raw unadjusted minute bars, bucket(32, symbol) + month(timestamp). **Frozen at 2026-06-12** — the flat-file subscription lapsed |
| `equities.polygon_daily_raw` | Polygon REST grouped-daily (nightly, paced) | Raw unadjusted daily bars; the live daily path now that flat-files lapsed |
| `equities.schwab_universe` | Schwab live + REST | Pre-adjusted (adj_factor=1.0); bucket(16) |
| `equities.schwab_daily_raw` | Schwab REST daily | Pre-adjusted daily tier |
| `equities.market_corp_actions` | Polygon REST | Dividends (+ splits, legacy); month(ex_date) partition |
| `equities.market_splits` | Polygon REST (mirrored from corp-actions) | Splits-only, ~27k rows; the read-time adjustment source. Tiny + symbol-sorted so per-symbol lookup is instant (no dividend scan) |

> **Split-adjusted OHLCV is computed at read time**, not stored — `adjusted =
> f(polygon_raw, market_splits)` via `app/services/equities/adjust.py`.
> The old materialized `equities.polygon_adjusted` table + its weekly Spark job
> were retired (see [`docs/data/adjusted_storage.md`](docs/data/adjusted_storage.md)).
> Readers (`AdjustedOhlcvReader`, `read_arrow` "polygon_adjusted" source) apply
> the adjustment on read.

Futures lake (separate `futures.*` Glue DB + `iceberg/futures/` S3):

| Table | Source | Notes |
|---|---|---|
| `futures.schwab_futures` | Schwab live + REST nightly | Continuous roots (/ES,…), recent ~48d 1-min; month(timestamp) partition |
| `futures.schwab_futures_daily` | Schwab REST | Deep daily tier for continuous roots |
| `futures.polygon_raw` | Polygon flat-file mirror → parse | Every outright contract (ESH4…), no roll applied |
| `futures.polygon_continuous` | `futures.polygon_raw` + volume roll | Back-adjusted continuous roots; `adj_factor` recovers true contract prices |

Options lake (separate `options.*` Glue DB + `iceberg/options/` S3):

| Table | Source | Notes |
|---|---|---|
| `options.schwab_chain_raw` | Schwab REST `/chains` | Raw provider payload per chain snapshot; audit/reparse source |
| `options.schwab_chain_contracts` | Schwab REST `/chains` | Canonical contract rows per snapshot; replay/backtest source |
| `options.schwab_expirations` | Schwab chain/expiration data | Observed expirations per underlying |
| `options.gamma_exposure_snapshots` | Derived from canonical contracts | GEX totals/levels by underlying, strike, expiry, strike+expiry |

ClickHouse (hot tier) — `ohlcv_1m` and `futures_ohlcv_1m` are the **only**
serving bar tables; every chart timeframe is resampled from them on read.
`ohlcv_daily` / `ohlcv_30m` / `ohlcv_hourly` are **research** tables built by
`scripts/build_ohlcv_*.py`, not on the serving path. `ohlcv_1m` covers only
the 300 symbols Schwab will stream, while `ohlcv_daily` covers 415 via REST —
**do not resample 1m into daily expecting full coverage.**

## Docs

`docs/` is organized by purpose; [`docs/README.md`](docs/README.md) is the
index.

| Dir | Holds |
|-----|-------|
| [`docs/standards/`](docs/standards/README.md) | **The rules — always read first** |
| [`docs/architecture/`](docs/architecture/README.md) | System map, lake schema, providers |
| [`docs/operations/`](docs/operations/runbook.md) | Runbook, startup flow, command cheatsheet |
| [`docs/data/`](docs/data/) | Data-platform specs: ingest, storage, read paths |
| [`docs/trading/`](docs/trading/) | Trading subsystem, models, signals |
| [`docs/research/`](docs/research/) | Validation methodology + findings |
| [`docs/product/`](docs/product/) | Customer-facing surfaces |
| [`docs/reference/`](docs/reference/schwab-api/) | Vendor API docs (not ours) |
| [`docs/ISSUES.md`](docs/ISSUES.md) | Live register — what is broken |
| [`docs/TODO.md`](docs/TODO.md) | Live register — what is deferred |

Hot paths:

| You need | Read |
|---|---|
| To run or fix the running system | [`operations/runbook.md`](docs/operations/runbook.md) |
| How data flows end to end | [`architecture/README.md`](docs/architecture/README.md) |
| The trading/backtest contract | [`trading/subsystem_design.md`](docs/trading/subsystem_design.md) |
| To make any performance claim | [`research/mcpt_methodology.md`](docs/research/mcpt_methodology.md) — pre-register in [`research/hypotheses.md`](docs/research/hypotheses.md) **before** the sweep |

Every module carries its own `README.md` — what it owns, its public contract,
how to test it. Read that before the cross-module docs.

Plan vs code conflict: code wins. The journal lives in detailed
commit messages; BUILD_JOURNAL was retired 2026-05-18 and the file
deleted 2026-05-21 (CV18).

## Infra

AWS only. Glue + S3 (Iceberg warehouse), CodeBuild for long backfills
(`scripts/codebuild/`). No CDK / Terraform. No Spark and no EMR — split
adjustment is computed at read time, and lake maintenance runs via Athena.
Self-hosted ClickHouse.
Secrets via `.env` (gitignored; template in `.env.example`, docs in
`CONFIG.md`).
