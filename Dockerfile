# syntax=docker/dockerfile:1.7
# StockAlert application image — one image, two roles (api / worker), chosen
# by env flags in infra/dev/docker-compose.yml. Spec: docs/operations/deployment_dev.md §6.
#
#   docker buildx build --platform linux/arm64 -t stockalert/app:<sha> .
#
# Deliberately NOT in the image: .env (every value must come from the
# container environment — an .env.example copy made unset variables silently
# take the example's values), docs/, data/ (except the committed seed), ewt/.

# ─── Stage 1: frontend ─────────────────────────────────────────────────────
# frontend/vite.config.ts writes the bundle to ../app/static/dist and
# FastAPI serves it at /app/. Build it here so the runtime stage needs no node.
FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# `npm run build` = tsc -b && vite build → /build/app/static/dist
RUN npm run build && test -f /build/app/static/dist/index.html

# ─── Stage 2: dependency pins ──────────────────────────────────────────────
# `pip install .` resolves from pyproject ranges and pip's backtracking lands
# on pyiceberg 0.7 (pyarrow<18) vs our pyarrow>=20 — unsolvable. poetry.lock
# is what the laptop runs and tests against, so the image installs exactly it.
FROM python:3.12-slim AS deps
RUN pip install --no-cache-dir poetry==2.1.4 poetry-plugin-export
WORKDIR /src
COPY pyproject.toml poetry.lock ./
RUN poetry export --only main --without-hashes -f requirements.txt -o /requirements.txt \
 && echo "exported $(grep -c . /requirements.txt) requirement lines"

# ─── Stage 3: runtime ──────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # The `app` package is served from /app, not pip-installed: uvicorn, alembic
    # and `python scripts/<job>.py` (whose sys.path[0] is scripts/) all need this.
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # PyIceberg multipart writes fail with BadDigest without this; must be set
    # before any boto3 client is built (scripts/codebuild/README.md).
    AWS_REQUEST_CHECKSUM_CALCULATION=when_required

# libgomp1: lightgbm runtime. curl: operator debugging inside the container
# (the HEALTHCHECK itself uses python so it works even if curl is removed).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Dependency layer from the lock (stage 2). Changes only when poetry.lock
# does, so code-only rebuilds reuse it.
COPY --from=deps /requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
 && python -c "import pyiceberg, pyarrow, fastapi, uvicorn, alembic, lightgbm; print('deps ok', pyiceberg.__version__, pyarrow.__version__)"

# Application code and the runtime assets the services read from disk:
#   scripts/      paper_daily_pipeline, backfills (docker compose run … worker)
#   migrations/ + alembic.ini   identity DB migrations (release runs them)
#   configs/      strategy/backtest YAML
#   data/market_events_seed.json   market calendar seed (app/services/market_events.py)
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY configs/ ./configs/
COPY data/market_events_seed.json ./data/market_events_seed.json
COPY --from=frontend /build/app/static/dist ./app/static/dist

# The Schwab provider may rewrite data/.schwab_refresh_token on rotation and
# the parquet cache (off by default) lives under data/ — the app user owns it.
RUN chown -R app:app /app
USER app

EXPOSE 8000

# Liveness only: /health answers 200 with clickhouse:false when CH is down, so
# a degraded CH does not restart the API in a loop (that is the Route 53 alarm's
# job). Python, not curl, so the check cannot silently pass on a missing binary.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4); sys.exit(0 if r.status == 200 else 1)"]

# Caddy is the only thing that can reach :8000, so trusting every proxy hop is
# safe and is what makes request.url report https behind TLS termination.
CMD ["uvicorn", "app.main_api:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
