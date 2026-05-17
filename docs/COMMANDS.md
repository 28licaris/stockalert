# Commands Cheatsheet

Quick reference for the commands you actually run on this repo. Run
everything from the repo root unless noted.

For *why* the system is structured this way see
[ARCHITECTURE.md](ARCHITECTURE.md). For *what's being built next* see
[BUILD_JOURNAL.md](BUILD_JOURNAL.md).

## First-time setup

```bash
cp .env.example .env             # fill in Polygon/Schwab/AWS keys
poetry install                   # installs deps into a venv
docker compose --profile ch up -d   # ClickHouse only (recommended local dev)
```

## Run the API

```bash
# Local (hot reload, what you'll use day-to-day)
poetry run uvicorn app.main_api:app --reload --host 127.0.0.1 --port 8000

# In Docker (API + ClickHouse together)
docker compose --profile full up -d
docker compose --profile full up           # foreground with logs
docker compose --profile full up --build   # after Dockerfile / deps change
```

API URLs:

| URL | Purpose |
|---|---|
| http://127.0.0.1:8000/dashboard | Main dashboard |
| http://127.0.0.1:8000/docs | OpenAPI / Swagger |
| http://127.0.0.1:8000/health | Liveness + CH ping |
| ws://127.0.0.1:8000/ws/signals | Push channel for fired signals |

## Docker compose

Two profiles exist:

| Profile | Brings up | Use case |
|---|---|---|
| `ch` | ClickHouse only | Local dev — API runs on host via `poetry run uvicorn` |
| `full` | ClickHouse + API | Test the full container stack |

```bash
# Start
docker compose --profile ch up -d        # CH only
docker compose --profile full up -d      # CH + API

# Status / logs
docker compose ps
docker compose logs -f clickhouse
docker compose logs -f api

# Restart after code change (full profile only — local uvicorn auto-reloads)
docker compose --profile full up -d --build

# Stop
docker compose stop                      # keeps containers + data
docker compose --profile full down       # removes containers, keeps data
docker compose --profile full down -v    # ⚠ also wipes chdata volume
```

Container names: `stockalert_clickhouse`, `stockalert_api`. Volume:
`chdata` (CH data persists here across container restarts).

## ClickHouse access

```bash
# Interactive client inside the CH container
docker exec -it stockalert_clickhouse clickhouse-client

# One-off query
docker exec stockalert_clickhouse \
  clickhouse-client --query "SELECT count() FROM stocks.ohlcv_1m"

# HTTP (used by the app)
curl 'http://localhost:8123/?query=SELECT+1'

# Smoke-check the schema is initialized
bash scripts/ch_verify.sh
```

## Tests

```bash
# Full suite (unit tests; skips integration without AWS creds)
poetry run pytest

# A single file or test
poetry run pytest tests/test_bronze_gaps.py -v
poetry run pytest tests/test_schwab_provider.py::TestChartContentToBar -v

# Integration tier (requires AWS creds + STOCK_LAKE_BUCKET in .env)
poetry run pytest -m integration

# Phase gate test (Iceberg connectivity)
poetry run pytest tests/integration/test_iceberg_connectivity.py -v
```

Known pre-existing failures + flaky tests are tracked in
[ISSUES.md](ISSUES.md), not regressions from recent commits.

## AWS / lake operations

Configure once (creates `~/.aws/credentials` profile `stock-lake`):

```bash
aws configure --profile stock-lake
aws sts get-caller-identity --profile stock-lake   # sanity check
```

```bash
# Provision (idempotent — safe to re-run)
bash scripts/provision_lake_infra.sh

# Inspect what's in S3 / Glue
poetry run python scripts/check_s3_lake.py
poetry run python scripts/check_polygon_flatfiles.py

# Bronze backfills
poetry run python scripts/polygon_bronze_backfill.py --help
poetry run python scripts/schwab_bronze_backfill.py --help

# Compaction (manual; refuses months >90d without --force)
poetry run python scripts/compact_bronze_monthly.py --month 2026-05
```

## Athena (lake queries)

```bash
# Count rows in bronze (cheap — uses Iceberg stats)
aws athena start-query-execution --profile stock-lake \
  --query-string "SELECT count(*) FROM stock_lake.polygon_minute" \
  --result-configuration "OutputLocation=s3://stock-lake-562741918372-us-east-1-an/athena-results/" \
  --work-group primary

# Easier: open the Athena console
open "https://us-east-1.console.aws.amazon.com/athena/home?region=us-east-1#/query-editor"
```

Dialect gotcha (also in
[memory:athena-ddl-dml-dialect](../docs/BUILD_JOURNAL.md)):
DDL uses Hive `` `backticks` ``, DML uses Trino `"double quotes"`. The
same query can't use both.

## Schwab OAuth

```bash
# One-time interactive flow — opens browser, writes ./data/.schwab_refresh_token
poetry run python scripts/schwab_get_refresh_token.py

# Verify the token works
poetry run python scripts/test_schwab_live.py
```

If the refresh token expires (Schwab rotates every 7 days), re-run
the first command.

## Polygon live test

```bash
poetry run python scripts/test_polygon_live.py
```

## Worktree / branch hygiene

This repo is set up to use Claude Code worktrees under `.claude/worktrees/`.

```bash
# See active worktrees
git worktree list

# Switch to a worktree for review
cd .claude/worktrees/<name>
```

## Common dev URLs

| URL | What |
|---|---|
| http://127.0.0.1:8000/dashboard | Live dashboard (charts + signals) |
| http://127.0.0.1:8000/journal | Trade journal page |
| http://127.0.0.1:8000/symbol/AAPL | Per-symbol drill-down |
| http://127.0.0.1:8000/docs | OpenAPI |
| http://localhost:8123 | ClickHouse HTTP (raw queries) |
| http://localhost:9000 | ClickHouse native protocol |
