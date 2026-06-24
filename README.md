# StockAlert

AI/ML-driven equities trading platform. **FastAPI** exposes REST + WebSockets and an **MCP** server for agents; **ClickHouse** is the hot tier (1-minute OHLCV, live/recent) and an **Iceberg data lake on S3** is the cold tier / ground truth (full split-adjusted history, ML). A configurable market-data provider (Alpaca, Polygon, or Schwab) streams bars in. On startup the app initializes the ClickHouse schema and runs a batched bar writer, **backfill** (with a periodic gap sweeper), **watchlist/stream** ingestion, optional **monitors** for divergence-style signals, **nightly lake refresh**, and **journal sync** (account balances + trades on a timer).

The dashboard is a **React + Vite** app under [`frontend/`](frontend/), served at `/app/`. (The old static HTML pages were removed — `frontend/` is the only UI.)

For architecture + design see [docs/](docs/README.md). The canonical lake/ML design is [`docs/architecture_v2/`](docs/architecture_v2/README.md); detailed phase history lives in commit messages (`git log --grep CV`). Env details in [CONFIG.md](CONFIG.md) and [.env.example](.env.example); Schwab helper flows in [scripts/README.md](scripts/README.md).

## Prerequisites

- Python 3.12 and [Poetry](https://python-poetry.org/)
- Node 20+ and npm (for the frontend)
- ClickHouse reachable at the host in `.env` (local Docker is typical)
- For lake/S3 features: an AWS profile named `stock-lake` in `~/.aws/credentials` (set `AWS_PROFILE=stock-lake` in `.env`)

## Setup

From the repo root:

```bash
cp .env.example .env   # then edit keys, CLICKHOUSE_*, and AWS_PROFILE
poetry install
```

Start ClickHouse only (profile `ch`):

```bash
docker compose --profile ch up -d
```

Start the lightweight customer-identity PostgreSQL container and apply its
schema (identity/auth development only):

```bash
docker compose --profile identity up -d postgres
poetry run alembic upgrade head
```

## Run locally (two servers)

**Backend API** — uvicorn on `:8000`:

```bash
poetry run uvicorn app.main_api:app --reload --host 127.0.0.1 --port 8000
```

**Frontend dashboard** — Vite dev server on `:5173` (proxies `/api`, `/mcp`, `/ws`, `/openapi.json` to the backend):

```bash
cd frontend
npm install        # first time only
npm run dev        # dashboard at http://localhost:5173/app/
```

Useful URLs:

| URL | Purpose |
|-----|---------|
| [http://localhost:5173/app/](http://localhost:5173/app/) | Dashboard (dev, via Vite) |
| [http://127.0.0.1:8000/app/](http://127.0.0.1:8000/app/) | Dashboard (after a frontend build — see below) |
| [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) | OpenAPI (Swagger) |
| [http://127.0.0.1:8000/mcp](http://127.0.0.1:8000/mcp) | MCP server (agent tools) |
| `GET /health` | Liveness + ClickHouse ping |
| `WS /ws/signals` | Push channel for signal payloads |

### Production build

`npm run build` compiles the frontend to `app/static/dist/`, which uvicorn then serves at `/app/` (same origin, so `/api` and `/ws` work without a proxy):

```bash
cd frontend && npm run build
# then the dashboard is live at http://127.0.0.1:8000/app/
```

When the backend's API types change, regenerate the typed client: `npm run codegen` (needs the backend running on `:8000`).

## Docker (API + ClickHouse + PostgreSQL)

Build and run both services (profile `full`; requires `.env` with provider keys as in compose):

```bash
docker compose --profile full up --build
```

## Tests

```bash
poetry run pytest                        # all
poetry run pytest -m "not integration"   # unit only (fast)
```

Identity repository integration tests use a separate disposable PostgreSQL
container:

```bash
docker compose --profile identity-test up -d postgres-test
TEST_IDENTITY_DATABASE_URL=postgresql+psycopg://stockalert:stockalert_test@localhost:5433/stockalert_identity_test \
  poetry run pytest tests/integration/test_identity_postgres.py
```

## Schwab one-off scripts

```bash
poetry run python scripts/schwab_get_refresh_token.py
poetry run python scripts/check_schwab_live.py
```

See [scripts/README.md](scripts/README.md) for OAuth and options.
