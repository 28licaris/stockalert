"""
FastAPI Application - Stock Divergence Alert System

Provides REST API and WebSocket endpoints for real-time divergence detection.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ErrorResponse

from app.db import (
    close_client,
    get_bar_batcher,
    init_schema,
    migrate_default_watchlist,
    ping,
    reset_bar_batcher,
)
from app.services.ingest.backfill_service import backfill_service
from app.services.jobs import audit_run, job_registry
from app.services.live.monitor_manager import monitor_manager
from app.services.live.watchlist_service import watchlist_service
from app.services.stream import stream_service
from app.api import (
    routes_backfill,
    routes_clickhouse,
    routes_corp_actions,
    routes_health,
    routes_indicators,
    routes_instruments,
    routes_jobs,
    routes_journal,
    routes_lake,
    routes_market,
    routes_monitors,
    routes_movers,
    routes_screener,
    routes_seed,
    routes_silver,
    routes_stream,
    routes_watchlist,
)
from app.services.journal.journal_sync import journal_sync_service

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

active_connections = []


async def _safe_start(label: str, coro_factory):
    """Run a subsystem startup in isolation: if it raises, log and continue.

    Service isolation is a deliberate design choice — a failure in any one
    subsystem (journal sync, watchlist, a nightly job) must not block the
    others or take down the FastAPI process. This wraps each `start()` call
    so the rest of the lifecycle proceeds.
    """
    try:
        return await coro_factory()
    except Exception as exc:
        logger.exception("✗ %s failed to start: %s — continuing without it", label, exc)
        return None


def _register_background_jobs(
    *,
    polygon_started: bool,
    schwab_started: bool,
    silver_started: bool,
    live_lake_writer_started: bool,
    journal_started: bool,
) -> None:
    """Catalog every running background loop with the JobRegistry.

    Each `name` matches the `ingestion_runs.job_name` written by the
    loop (so `JobRegistry.list()` can join last_success). `run_now`
    callables either reuse the loop's existing one-cycle function (for
    loops that self-audit) or wrap a one-shot call in
    `audit_run(name)` (for loops that don't). Adding a new background
    loop = add one block here + write to `ingestion_runs` from the
    loop OR wrap with `audit_run`.
    """
    from app.config import settings as _s

    # Backfill gap sweeper — sync `sweep_now` doesn't self-audit, wrap it.
    async def _run_gap_sweeper_once() -> None:
        async with audit_run("backfill_gap_sweeper"):
            await asyncio.to_thread(backfill_service.sweep_now)

    job_registry.register(
        name="backfill_gap_sweeper",
        display_name="Backfill gap sweeper",
        schedule="daily at 06:00 UTC (7d window)",
        setting_key=None,
        run_now=_run_gap_sweeper_once,
    )

    # Live lake writer — `run_cycle` already writes to ingestion_runs,
    # so the manual run just invokes it without an audit wrapper.
    if live_lake_writer_started:
        async def _run_live_lake_writer_once() -> None:
            from app.services.ingest.live_lake_writer import get_live_lake_writer

            await get_live_lake_writer().run_cycle()

        job_registry.register(
            name="live_lake_writer",
            display_name="Live lake writer",
            schedule=f"every {_s.live_lake_writer_cycle_minutes} min",
            setting_key="LIVE_LAKE_WRITER_CYCLE_MINUTES",
            run_now=_run_live_lake_writer_once,
        )

    # Nightly Polygon — refresh_polygon_lake_yesterday doesn't audit.
    if polygon_started:
        async def _run_polygon_once() -> None:
            from app.services.ingest.nightly_polygon_refresh import (
                refresh_polygon_lake_yesterday,
            )

            async with audit_run("nightly_polygon_refresh"):
                await refresh_polygon_lake_yesterday()

        job_registry.register(
            name="nightly_polygon_refresh",
            display_name="Nightly Polygon refresh",
            schedule=f"daily at {int(_s.polygon_nightly_run_hour_utc):02d}:00 UTC",
            setting_key="POLYGON_NIGHTLY_RUN_HOUR_UTC",
            run_now=_run_polygon_once,
        )

    # Nightly Schwab — refresh_schwab_bronze_yesterday doesn't audit.
    if schwab_started:
        async def _run_schwab_once() -> None:
            from app.services.ingest.nightly_schwab_refresh import (
                refresh_schwab_bronze_yesterday,
            )

            async with audit_run("nightly_schwab_refresh"):
                await refresh_schwab_bronze_yesterday()

        job_registry.register(
            name="nightly_schwab_refresh",
            display_name="Nightly Schwab refresh",
            schedule=f"daily at {int(_s.schwab_nightly_run_hour_utc):02d}:00 UTC",
            setting_key="SCHWAB_NIGHTLY_RUN_HOUR_UTC",
            run_now=_run_schwab_once,
        )

    # Silver OHLCV build — run_silver_ohlcv_build_nightly self-audits.
    if silver_started:
        async def _run_silver_ohlcv_build_once() -> None:
            from app.services.silver.ohlcv.nightly import (
                run_silver_ohlcv_build_nightly,
            )

            await run_silver_ohlcv_build_nightly()

        job_registry.register(
            name="silver_ohlcv_build",
            display_name="Silver OHLCV build",
            schedule=f"daily at {int(_s.silver_ohlcv_build_run_hour_utc):02d}:00 UTC",
            setting_key="SILVER_OHLCV_BUILD_RUN_HOUR_UTC",
            run_now=_run_silver_ohlcv_build_once,
        )

    # Journal sync — `sync_all` doesn't write ingestion_runs, wrap it.
    if journal_started:
        from app.services.journal.journal_sync import journal_sync_service as _js

        async def _run_journal_sync_once() -> None:
            async with audit_run("journal_sync"):
                await _js.sync_all(force=True)

        job_registry.register(
            name="journal_sync",
            display_name="Journal sync (Schwab)",
            schedule="every 5 min",
            setting_key=None,
            run_now=_run_journal_sync_once,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting StockAlert API...")

    # Foundation tasks — these are intentionally NOT isolated. If the
    # ClickHouse schema can't init or the bar batcher can't start, the
    # app genuinely has nothing useful to serve, and a hard fail surfaces
    # the problem immediately.
    await asyncio.to_thread(init_schema)
    logger.info("✅ ClickHouse schema ready")

    migration = await asyncio.to_thread(migrate_default_watchlist)
    if migration.get("migrated"):
        logger.info(
            "✅ Default watchlist migrated from %s (symbols=%d)",
            migration.get("source") or "(empty)",
            len(migration.get("symbols") or []),
        )

    batcher = get_bar_batcher()
    await batcher.start()
    logger.info("✅ OHLCV batch writer started")

    # ── Subsystem startup, each isolated ───────────────────────────────
    # One subsystem's failure must not affect the others.

    backfill_started = await _safe_start(
        "Backfill service", lambda: backfill_service.start()
    )
    if backfill_started is not None or True:
        # backfill_service.start() returns None on success; the gap sweeper
        # is wired below only if start succeeded.
        try:
            status = backfill_service.status() if hasattr(backfill_service, "status") else None
            if status is None:
                logger.info("✅ Backfill service ready")
        except Exception:
            logger.info("✅ Backfill service ready")

    # Stream service starts first — it owns Schwab subscriptions and the
    # stream_universe table. Watchlist service depends on it (auto-extend
    # on add) so order matters.
    await _safe_start("Stream service", lambda: stream_service.start())
    try:
        s_status = stream_service.status()
        logger.info(
            "✅ Stream service started (provider=%s, universe=%d, subscribed=%d)",
            s_status["provider"],
            s_status.get("universe_count", 0),
            s_status.get("streaming_count", 0),
        )
        if s_status.get("provider_error"):
            logger.warning("Stream provider error: %s", s_status["provider_error"])
    except Exception as e:
        logger.warning("stream_service.status() failed: %s", e)

    await _safe_start("Watchlist service", lambda: watchlist_service.start())
    try:
        status = watchlist_service.status()
        logger.info(
            "✅ Watchlist service started (CRUD-only; watchlists=%d, default-members=%d)",
            status.get("watchlist_count", 0),
            status.get("symbol_count", 0),
        )
    except Exception as e:
        logger.warning("watchlist_service.status() failed: %s", e)

    # Live lake writer (TA-5.7): every cycle_minutes, flushes live-stream
    # ohlcv_1m rows from CH into bronze.{provider}_minute. Closes the
    # 8-24h freshness gap that the bronze audit (2026-05-17) identified.
    # Gated by LIVE_LAKE_WRITER_ENABLED so operators can disable for
    # CH-only setups.
    from app.config import settings as _llw_settings
    if _llw_settings.live_lake_writer_enabled:
        from app.services.ingest.live_lake_writer import start_live_lake_writer
        await _safe_start("Live lake writer", start_live_lake_writer)
        logger.info(
            "✅ Live lake writer started (cycle=%dmin lookback=%dmin)",
            _llw_settings.live_lake_writer_cycle_minutes,
            _llw_settings.live_lake_writer_lookback_minutes,
        )
    else:
        logger.info("ℹ️  Live lake writer disabled (LIVE_LAKE_WRITER_ENABLED=false)")

    # Wire the periodic gap sweeper: every 15 min the backfill service will
    # ask `stream_service` for the current streaming set and auto-enqueue
    # gap-fill jobs for any symbol with within-session holes.
    def _streaming_symbols_for_sweeper() -> list[str]:
        try:
            return list(stream_service.status().get("streaming_symbols") or [])
        except Exception as e:
            logger.warning("gap sweeper: could not enumerate streaming symbols: %s", e)
            return []
    try:
        backfill_service.set_symbol_provider(_streaming_symbols_for_sweeper)
        logger.info("✅ Backfill gap sweeper armed (daily at 06:00 UTC, 7d window)")
    except Exception as e:
        logger.warning("gap sweeper arming failed: %s", e)

    # Kick a one-shot sweep shortly after startup so any holes that opened up
    # while the app was down (or while a provider switch was in flight) get
    # repaired immediately instead of waiting until the next 06:00 UTC sweep.
    # The per-symbol `gap_fill` throttle (6h cooldown) makes this a free no-op
    # if a sweep already ran recently, so rapid restarts don't hammer the
    # provider.
    async def _initial_gap_sweep_after_warmup() -> None:
        # Let the watchlist subscribe + a couple of live bars land before we
        # scan. 30s is well inside the user's "Refresh" loop and outside the
        # tightest WS-handshake window.
        try:
            await asyncio.sleep(30.0)
            result = backfill_service.sweep_now()
            logger.info("✅ Initial gap sweep complete: %s", result)
        except Exception as e:
            logger.warning("Initial gap sweep failed: %s", e)
    asyncio.create_task(_initial_gap_sweep_after_warmup(),
                        name="backfill_initial_sweep")

    # Journal sync is Schwab-only — gate it behind both an explicit toggle and
    # the presence of Schwab credentials so users running on other providers
    # (e.g. DATA_PROVIDER=polygon) don't get a noisy 5-minute warning loop.
    from app.config import settings as _settings
    _journal_has_creds = bool(
        _settings.schwab_client_id and _settings.schwab_client_secret
        and _settings.get_schwab_refresh_token()
    )
    if _settings.journal_enabled and _journal_has_creds:
        await _safe_start("Journal sync", lambda: journal_sync_service.start())
        logger.info("✅ Journal sync started (every 5min: balances + trades)")
    elif not _settings.journal_enabled:
        logger.info("ℹ️  Journal sync disabled (JOURNAL_ENABLED=false)")
    else:
        logger.info(
            "ℹ️  Journal sync skipped (missing Schwab credentials; "
            "set SCHWAB_CLIENT_ID/SECRET + refresh token to enable)"
        )

    nightly_lake_task: asyncio.Task | None = None
    if _settings.polygon_nightly_enabled and (_settings.stock_lake_bucket or "").strip():
        try:
            from app.services.ingest.nightly_polygon_refresh import run_lake_refresh_loop

            nightly_lake_task = asyncio.create_task(
                run_lake_refresh_loop(),
                name="nightly_polygon_refresh",
            )
            app.state.nightly_lake_task = nightly_lake_task
            logger.info(
                "nightly_polygon_refresh: background loop started "
                "(POLYGON_NIGHTLY_RUN_HOUR_UTC=%s, symbols=%s)",
                _settings.polygon_nightly_run_hour_utc,
                _settings.polygon_nightly_symbols,
            )
        except Exception as exc:
            logger.exception(
                "✗ nightly_polygon_refresh failed to start: %s — continuing without it",
                exc,
            )

    nightly_schwab_task: asyncio.Task | None = None
    if _settings.schwab_nightly_enabled and (_settings.stock_lake_bucket or "").strip():
        try:
            from app.services.ingest.nightly_schwab_refresh import run_schwab_refresh_loop

            nightly_schwab_task = asyncio.create_task(
                run_schwab_refresh_loop(),
                name="nightly_schwab_refresh",
            )
            app.state.nightly_schwab_task = nightly_schwab_task
            logger.info(
                "nightly_schwab_refresh: background loop started "
                "(SCHWAB_NIGHTLY_RUN_HOUR_UTC=%s, symbols=%s)",
                _settings.schwab_nightly_run_hour_utc,
                _settings.schwab_nightly_symbols,
            )
        except Exception as exc:
            logger.exception(
                "✗ nightly_schwab_refresh failed to start: %s — continuing without it",
                exc,
            )

    # Nightly silver OHLCV build (TA-5.1.6). Runs after both upstream
    # nightlies (polygon 07:00 UTC, schwab 22:00 UTC) — default 23:00
    # UTC gives Schwab nightly headroom. Idempotent + isolated-failure.
    nightly_silver_ohlcv_task: asyncio.Task | None = None
    if (
        getattr(_settings, "silver_ohlcv_build_enabled", False)
        and (_settings.stock_lake_bucket or "").strip()
    ):
        try:
            from app.services.silver.ohlcv.nightly import run_silver_ohlcv_build_loop

            nightly_silver_ohlcv_task = asyncio.create_task(
                run_silver_ohlcv_build_loop(),
                name="nightly_silver_ohlcv_build",
            )
            app.state.nightly_silver_ohlcv_task = nightly_silver_ohlcv_task
            logger.info(
                "nightly_silver_ohlcv_build: background loop started "
                "(SILVER_OHLCV_BUILD_RUN_HOUR_UTC=%s, symbols=%s)",
                _settings.silver_ohlcv_build_run_hour_utc,
                _settings.silver_ohlcv_build_symbols,
            )
        except Exception as exc:
            logger.exception(
                "✗ nightly_silver_ohlcv_build failed to start: %s — continuing without it",
                exc,
            )

    async def broadcast_signal(signal_data: dict):
        logger.info(f"📡 Broadcasting signal: {signal_data}")
        disconnected = []
        for ws in active_connections:
            try:
                await ws.send_json(signal_data)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                disconnected.append(ws)
        for ws in disconnected:
            if ws in active_connections:
                active_connections.remove(ws)

    # ── Job registry ──────────────────────────────────────────────────
    # Register each background loop with the JobRegistry so the cockpit
    # Status page can list it (with last-success from `ingestion_runs`)
    # and offer a manual "run now" button. Registration mirrors the
    # conditional starts above — we only register a job if its loop was
    # actually launched. The `run_now` callables either invoke the
    # loop's own one-cycle entry point (which self-audits) or are
    # wrapped in `audit_run(...)` so each manual run lands one
    # `ingestion_runs` row uniformly.
    _register_background_jobs(
        polygon_started=nightly_lake_task is not None,
        schwab_started=nightly_schwab_task is not None,
        silver_started=nightly_silver_ohlcv_task is not None,
        live_lake_writer_started=_llw_settings.live_lake_writer_enabled,
        journal_started=_settings.journal_enabled and _journal_has_creds,
    )

    app.state.broadcast_signal = broadcast_signal
    logger.info("✅ Application startup complete")

    yield

    logger.info("🛑 Shutting down StockAlert API...")
    lt = getattr(app.state, "nightly_lake_task", None)
    if lt is not None and not lt.done():
        lt.cancel()
        try:
            await lt
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("nightly_lake_refresh task shutdown: %s", e)

    st = getattr(app.state, "nightly_schwab_task", None)
    if st is not None and not st.done():
        st.cancel()
        try:
            await st
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("nightly_schwab_refresh task shutdown: %s", e)

    sov = getattr(app.state, "nightly_silver_ohlcv_task", None)
    if sov is not None and not sov.done():
        sov.cancel()
        try:
            await sov
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("nightly_silver_ohlcv_build task shutdown: %s", e)

    # Live lake writer: stop BEFORE watchlist (so it can capture any
    # last-minute streamed bars; the stop call gives the in-flight cycle
    # up to 5s to drain).
    try:
        from app.services.ingest.live_lake_writer import stop_live_lake_writer
        await stop_live_lake_writer()
        logger.info("✅ Live lake writer stopped")
    except Exception as e:
        logger.warning("live_lake_writer stop failed: %s", e)

    await monitor_manager.stop_all()
    logger.info("✅ Monitors stopped")

    await watchlist_service.stop()
    logger.info("✅ Watchlist service stopped")

    await stream_service.stop()
    logger.info("✅ Stream service stopped")

    await backfill_service.stop()
    logger.info("✅ Backfill service stopped")

    # Symmetric guard: only stop if we actually started it above. The service
    # is a singleton, so calling stop on an unstarted instance is safe; we just
    # skip the log line to avoid lying about state.
    if journal_sync_service._started:  # type: ignore[attr-defined]
        await journal_sync_service.stop()
        logger.info("✅ Journal sync stopped")

    await get_bar_batcher().stop()
    reset_bar_batcher()
    logger.info("✅ OHLCV batch writer stopped")

    close_client()
    logger.info("✅ ClickHouse client closed")
    logger.info("✅ Shutdown complete")


app = FastAPI(
    title="StockAlert API",
    description="Real-time stock divergence detection and alerting system",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────
# Error envelope handlers (FE-CONTRACTS-1)
# Convert FastAPI's default `{"detail": "..."}` shape into the typed
# ErrorResponse defined in app/api/schemas/common.py. Components in
# the cockpit consume `code` + `message` + `details` directly.
#
# Status-code → error-code mapping is best-effort; route handlers may
# pass a custom code via `HTTPException(..., headers={"X-Error-Code": "..."})`.
# ─────────────────────────────────────────────────────────────────────


_STATUS_CODE_DEFAULT: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    422: "unprocessable",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def _error_code_for(status_code: int, override: str | None = None) -> str:
    if override:
        return override
    return _STATUS_CODE_DEFAULT.get(status_code, "error")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Wrap raised HTTPExceptions in the typed ErrorResponse envelope."""
    del request  # request_id middleware lands in a later phase
    # `detail` may already be a dict (route called HTTPException(detail={...}));
    # in that case treat it as structured details with a generic message.
    if isinstance(exc.detail, dict):
        message = str(exc.detail.get("message") or exc.detail.get("error") or "Error")
        details = exc.detail
    else:
        message = str(exc.detail) if exc.detail is not None else "Error"
        details = None

    code_override = exc.headers.get("X-Error-Code") if exc.headers else None
    envelope = ErrorResponse(
        code=_error_code_for(exc.status_code, code_override),
        message=message,
        details=details,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope.model_dump(exclude_none=False),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 from request-body / query-param validation → ErrorResponse."""
    del request
    envelope = ErrorResponse(
        code="validation_error",
        message="Request validation failed.",
        details={"errors": exc.errors()},
    )
    return JSONResponse(
        status_code=422,
        content=envelope.model_dump(exclude_none=False),
    )

# ─────────────────────────────────────────────────────────────────────
# Router mounts (FE-CONTRACTS-1: one-shot rename to /api/v1/*)
# Legacy paths get 307 redirects further down — see "legacy redirects".
# ─────────────────────────────────────────────────────────────────────

_V1 = "/api/v1"

app.include_router(routes_health.router, prefix=_V1, tags=["Health"])
app.include_router(routes_movers.router, prefix=_V1, tags=["Movers"])
app.include_router(routes_backfill.router, prefix=_V1, tags=["Backfill"])
app.include_router(routes_instruments.router, prefix=_V1, tags=["Instruments"])
app.include_router(routes_market.router, prefix=_V1, tags=["Market"])
app.include_router(routes_journal.router, prefix=_V1, tags=["Journal"])
app.include_router(routes_lake.router, prefix=_V1, tags=["Lake"])
app.include_router(routes_indicators.router, prefix=_V1, tags=["Indicators"])
app.include_router(routes_screener.router, prefix=_V1, tags=["Screener"])
app.include_router(routes_corp_actions.router, prefix=_V1, tags=["CorpActions"])
app.include_router(routes_silver.router, prefix=_V1, tags=["Silver"])
app.include_router(routes_monitors.router, prefix=_V1, tags=["Monitors"])
app.include_router(routes_watchlist.router, prefix=_V1, tags=["Watchlist"])
app.include_router(routes_seed.router, prefix=_V1, tags=["Seed"])
app.include_router(routes_stream.router, prefix=_V1, tags=["Stream"])
app.include_router(routes_jobs.router, prefix=_V1, tags=["Jobs"])
app.include_router(routes_clickhouse.router, prefix=_V1, tags=["ClickHouse"])

try:
    from app.api import routes_signals
    app.include_router(routes_signals.router, prefix=_V1, tags=["Signals"])
    logger.info("✅ Signals API routes loaded")
except ImportError:
    logger.info("ℹ️  Signals routes not available")

try:
    from app.api import routes_backtest
    app.include_router(routes_backtest.router, prefix=_V1, tags=["Backtest"])
    logger.info("✅ Backtest routes loaded")
except ImportError:
    logger.info("ℹ️  Backtest routes not available")

try:
    from app.api import routes_assistant
    app.include_router(
        routes_assistant.router,
        prefix="/cockpit/assistant",
        tags=["Assistant"],
    )
    logger.info("✅ Assistant routes loaded")
except Exception as _asst_exc:  # noqa: BLE001
    logger.warning("ℹ️  Assistant routes not mounted: %s", _asst_exc)


# ─────────────────────────────────────────────────────────────────────
# Legacy redirects (FE-CONTRACTS-1)
# Every legacy path returns 307 → /api/v1/<same path tail>. 307
# preserves both method and body, so legacy HTML POSTs to /watchlist/add
# arrive at /api/v1/watchlist/add intact.
#
# Registered AFTER all /api/v1 routes so the v1 routes match first
# (Starlette routing is first-match-wins).
#
# Deletion plan: tracked in [docs/frontend_api_contracts.md §10.2] —
# legacy redirects removed when the last static HTML page is gone or
# at FE-CONTRACTS-7, whichever comes first.
# ─────────────────────────────────────────────────────────────────────


def _v1_redirect(target_path: str, request: Request) -> RedirectResponse:
    """Preserve the query string when redirecting; 307 preserves method+body."""
    qs = request.url.query
    url = f"{target_path}?{qs}" if qs else target_path
    return RedirectResponse(url=url, status_code=307)


@app.api_route(
    "/api/{rest:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def legacy_api_redirect(rest: str, request: Request):
    # Defensive: if `/api/v1/<x>` falls through here it means a v1 route
    # is missing; let it 404 cleanly rather than redirect into a loop.
    if rest.startswith("v1/") or rest == "v1":
        raise HTTPException(status_code=404, detail="Not Found")
    return _v1_redirect(f"/api/v1/{rest}", request)


# The single-watchlist legacy routes lived at root (/watchlist, /watchlist/add,
# /watchlist/remove, /watchlist/snapshot). They're still called by symbol.html
# and dashboard.html. Map each to its new /api/v1 home.
@app.api_route(
    "/watchlist",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def legacy_watchlist_root_redirect(request: Request):
    return _v1_redirect("/api/v1/watchlist", request)


@app.api_route(
    "/watchlist/{rest:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def legacy_watchlist_redirect(rest: str, request: Request):
    return _v1_redirect(f"/api/v1/watchlist/{rest}", request)


# ─────────────────────────────────────────────────────────────────────
# MCP server (Pre-Phase 3 Step 3). Mounted at /mcp — same readers that
# back HTTP routes back the MCP tools, so agents and humans see the
# same Pydantic shapes.
# ─────────────────────────────────────────────────────────────────────
try:
    from app.mcp.server import mount_on as _mount_mcp
    _mount_mcp(app)
except Exception as _mcp_exc:  # noqa: BLE001 — boundary; isolate startup failure
    logger.warning("ℹ️  MCP server not mounted: %s", _mcp_exc)


_DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
_SYMBOL_PATH = os.path.join(os.path.dirname(__file__), "static", "symbol.html")
_JOURNAL_PATH = os.path.join(os.path.dirname(__file__), "static", "journal.html")

# React cockpit (FE-1+). Vite builds to app/static/dist/. Purely
# additive: legacy /dashboard, /symbol/{ticker}, /journal stay
# unchanged. The cockpit is reachable at /app/ only after a build.
_COCKPIT_DIST = os.path.join(os.path.dirname(__file__), "static", "dist")
_COCKPIT_INDEX = os.path.join(_COCKPIT_DIST, "index.html")
_COCKPIT_AVAILABLE = os.path.isfile(_COCKPIT_INDEX)

if _COCKPIT_AVAILABLE:
    # Hashed JS/CSS/etc. under /app/assets — direct StaticFiles.
    app.mount(
        "/app/assets",
        StaticFiles(directory=os.path.join(_COCKPIT_DIST, "assets")),
        name="cockpit-assets",
    )

    @app.get("/app", include_in_schema=False)
    @app.get("/app/", include_in_schema=False)
    @app.get("/app/{full_path:path}", include_in_schema=False)
    async def cockpit_spa(full_path: str = ""):
        # SPA fallback: every route under /app/ renders index.html so
        # React Router can resolve it client-side. Static asset
        # requests are caught by the /app/assets mount above.
        del full_path
        return FileResponse(_COCKPIT_INDEX, media_type="text/html")
else:
    logger.info(
        "ℹ️  Cockpit build not found at %s — run `cd frontend && npm run build` "
        "to enable /app/. Legacy /dashboard, /symbol, /journal continue to work.",
        _COCKPIT_DIST,
    )


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return FileResponse(_DASHBOARD_PATH, media_type="text/html")


@app.get("/symbol/{ticker}", include_in_schema=False)
async def symbol_page(ticker: str):
    return FileResponse(_SYMBOL_PATH, media_type="text/html")


@app.get("/journal", include_in_schema=False)
async def journal_page():
    return FileResponse(_JOURNAL_PATH, media_type="text/html")


@app.get("/health")
async def health():
    ok = await asyncio.to_thread(ping)
    return {
        "status": "ok" if ok else "degraded",
        "clickhouse": ok,
    }


@app.get("/stats")
async def stats():
    from app.db import queries

    total_bars, total_signals, recent = await asyncio.gather(
        asyncio.to_thread(queries.count_bars),
        asyncio.to_thread(queries.count_signals),
        asyncio.to_thread(queries.recent_signals, 5),
    )

    def _ts(v):
        """Force-stamp naive ClickHouse datetimes with `Z` so JS parses them as UTC."""
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            if getattr(v, "tzinfo", None) is None:
                return v.isoformat() + "Z"
            return v.isoformat()
        return str(v)

    return {
        "total_bars": total_bars,
        "total_signals": total_signals,
        "recent_signals": [
            {
                "symbol": s["symbol"],
                "type": s["type"],
                "indicator": s["indicator"],
                "price": s["price"],
                "timestamp": _ts(s["ts"]),
            }
            for s in recent
        ]
    }


@app.websocket("/ws/signals")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"WebSocket connected. Total: {len(active_connections)}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(active_connections)}")
