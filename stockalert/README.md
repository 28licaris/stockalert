# StockAlert

Backend for a stock divergence / monitoring stack: **FastAPI** exposes REST and WebSockets, **ClickHouse** stores 1-minute OHLCV and related data, and a configurable **market data provider** (Alpaca, Polygon, or Schwab) streams bars into the database. On startup the app initializes the schema, runs a batched bar writer, **backfill** (including a periodic gap sweeper), **watchlist** streaming, optional **monitors** for divergence-style signals, and **journal sync** (account balances and trades on a timer). Static HTML pages under `/dashboard`, `/journal`, and `/symbol/{ticker}` sit beside the API.

For architecture + active build plans see [docs/](docs/README.md) — start with `docs/BUILD_JOURNAL.md` to pick up where the build left off. Env details in [CONFIG.md](CONFIG.md) and [.env.example](.env.example); Schwab helper flows in [scripts/README.md](scripts/README.md).

## Prerequisites

- Python 3.9+ and [Poetry](https://python-poetry.org/)
- ClickHouse reachable at the host in `.env` (local Docker is typical)

## Setup

From the **git repository root**, enter the application directory (the one that contains `pyproject.toml` and `docker-compose.yml`—often `stockalert/` inside the clone):

```bash
cd stockalert
cp .env.example .env   # then edit keys and CLICKHOUSE_*
poetry install
```

Start ClickHouse only (profile `ch`):

```bash
docker compose --profile ch up -d
```

## Run the API locally

```bash
poetry run uvicorn app.main_api:app --reload --host 127.0.0.1 --port 8000
```

Useful URLs:

| URL | Purpose |
|-----|---------|
| [http://127.0.0.1:8000/dashboard](http://127.0.0.1:8000/dashboard) | Main dashboard |
| [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) | OpenAPI (Swagger) |
| `GET /health` | Liveness + ClickHouse ping |
| `WS /ws/signals` | Push channel for signal payloads |

## Docker (API + ClickHouse)

Build and run both services (profile `full`; requires `.env` with provider keys as in compose):

```bash
docker compose --profile full up --build
```

## Tests

```bash
poetry run pytest
```

## Schwab one-off scripts

```bash
poetry run python scripts/schwab_get_refresh_token.py
poetry run python scripts/test_schwab_live.py
```

See [scripts/README.md](scripts/README.md) for OAuth and options.
