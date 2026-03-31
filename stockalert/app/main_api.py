"""
FastAPI Application - Stock Divergence Alert System

Provides REST API and WebSocket endpoints for real-time divergence detection.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.db import (
    close_client,
    get_bar_batcher,
    init_schema,
    ping,
    reset_bar_batcher,
)
from app.services.monitor_manager import monitor_manager
from app.api import routes_monitors

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

    batcher = get_bar_batcher()
    await batcher.start()
    logger.info("✅ OHLCV batch writer started")

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


@app.get("/")
async def root():
    return {
        "status": "running",
        "service": "StockAlert API",
        "version": "0.1.0"
    }


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
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

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
