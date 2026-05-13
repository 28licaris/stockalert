"""
FastAPI Application - Stock Divergence Alert System

Provides REST API and WebSocket endpoints for real-time divergence detection.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from app.db import (
    close_client,
    get_bar_batcher,
    init_schema,
    migrate_default_watchlist,
    ping,
    reset_bar_batcher,
)
from app.services.backfill_service import backfill_service
from app.services.monitor_manager import monitor_manager
from app.services.watchlist_service import watchlist_service
from app.api import (
    routes_backfill,
    routes_instruments,
    routes_journal,
    routes_market,
    routes_monitors,
    routes_movers,
    routes_watchlist,
)
from app.services.journal_sync import journal_sync_service

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

active_connections = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting StockAlert API...")

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

    await backfill_service.start()
    logger.info("✅ Backfill service ready")

    await watchlist_service.start()
    status = watchlist_service.status()
    logger.info(
        "✅ Watchlist service started (provider=%s, symbols=%d, streaming=%d)",
        status["provider"], status["symbol_count"], status.get("subscribed_count", 0),
    )

    # Wire the periodic gap sweeper: every 15 min the backfill service will
    # ask `watchlist_service` for the current streaming set and auto-enqueue
    # gap-fill jobs for any symbol with within-session holes. Importing here
    # (rather than at module top) avoids constructing the service at import
    # time when env vars may not yet be loaded.
    def _streaming_symbols_for_sweeper() -> list[str]:
        try:
            return list(watchlist_service.status().get("streaming_symbols") or [])
        except Exception as e:
            logger.warning("gap sweeper: could not enumerate streaming symbols: %s", e)
            return []
    backfill_service.set_symbol_provider(_streaming_symbols_for_sweeper)
    logger.info("✅ Backfill gap sweeper armed (daily at 06:00 UTC, 7d window)")

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
        await journal_sync_service.start()
        logger.info("✅ Journal sync started (every 5min: balances + trades)")
    elif not _settings.journal_enabled:
        logger.info("ℹ️  Journal sync disabled (JOURNAL_ENABLED=false)")
    else:
        logger.info(
            "ℹ️  Journal sync skipped (missing Schwab credentials; "
            "set SCHWAB_CLIENT_ID/SECRET + refresh token to enable)"
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

    app.state.broadcast_signal = broadcast_signal
    logger.info("✅ Application startup complete")

    yield

    logger.info("🛑 Shutting down StockAlert API...")
    await monitor_manager.stop_all()
    logger.info("✅ Monitors stopped")

    await watchlist_service.stop()
    logger.info("✅ Watchlist service stopped")

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

app.include_router(routes_monitors.router, prefix="", tags=["Monitors"])
app.include_router(routes_watchlist.router, prefix="", tags=["Watchlist"])
app.include_router(routes_movers.router, prefix="/api", tags=["Movers"])
app.include_router(routes_backfill.router, prefix="/api", tags=["Backfill"])
app.include_router(routes_instruments.router, prefix="/api", tags=["Instruments"])
app.include_router(routes_market.router, prefix="/api", tags=["Market"])
app.include_router(routes_journal.router, prefix="/api", tags=["Journal"])

try:
    from app.api import routes_signals
    app.include_router(routes_signals.router, prefix="/api", tags=["Signals"])
    logger.info("✅ Signals API routes loaded")
except ImportError:
    logger.info("ℹ️  Signals routes not available")

try:
    from app.api import routes_backtest
    app.include_router(routes_backtest.router, prefix="/api", tags=["Backtest"])
    logger.info("✅ Backtest routes loaded")
except ImportError:
    logger.info("ℹ️  Backtest routes not available")


_DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
_SYMBOL_PATH = os.path.join(os.path.dirname(__file__), "static", "symbol.html")
_JOURNAL_PATH = os.path.join(os.path.dirname(__file__), "static", "journal.html")


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
