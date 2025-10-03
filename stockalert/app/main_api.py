"""
FastAPI Application - Stock Divergence Alert System

Provides REST API and WebSocket endpoints for real-time divergence detection.
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging

from app.db import init_db, close_db
from app.services.monitor_manager import monitor_manager
from app.api import routes_monitors

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# WebSocket connections
active_connections = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Handles startup and shutdown events:
    - Startup: Initialize database, setup broadcast callback
    - Shutdown: Stop all monitors, close database connections
    """
    # === STARTUP ===
    logger.info("🚀 Starting StockAlert API...")
    
    # Initialize database
    await init_db()
    logger.info("✅ Database initialized")
    
    # Setup WebSocket broadcast callback
    async def broadcast_signal(signal_data: dict):
        """
        Broadcast signal to all connected WebSocket clients.
        
        Args:
            signal_data: Signal information dict
        """
        logger.info(f"📡 Broadcasting signal: {signal_data}")
        
        # Send to all connected clients
        disconnected = []
        for ws in active_connections:
            try:
                await ws.send_json(signal_data)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                disconnected.append(ws)
        
        # Remove disconnected clients
        for ws in disconnected:
            if ws in active_connections:
                active_connections.remove(ws)
    
    # Store broadcast callback in app state
    app.state.broadcast_signal = broadcast_signal
    
    logger.info("✅ Application startup complete")
    
    yield
    
    # === SHUTDOWN ===
    logger.info("🛑 Shutting down StockAlert API...")
    
    # Stop all active monitors
    await monitor_manager.stop_all()
    logger.info("✅ Monitors stopped")
    
    # Close database connections
    await close_db()
    logger.info("✅ Database closed")
    
    logger.info("✅ Shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="StockAlert API",
    description="Real-time stock divergence detection and alerting system",
    version="0.1.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(routes_monitors.router, prefix="", tags=["Monitors"])

# Try to include optional routers (if they exist)
try:
    from app.api import routes_signals, routes_backtest
    app.include_router(routes_signals.router, prefix="/api", tags=["Signals"])
    app.include_router(routes_backtest.router, prefix="/api", tags=["Backtest"])
    logger.info("✅ Optional routes loaded")
except ImportError:
    logger.info("ℹ️  Optional routes not available")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "service": "StockAlert API",
        "version": "0.1.0"
    }


@app.get("/stats")
async def stats():
    """
    Get database statistics.
    
    Returns:
        Dict with bar count, signal count, and recent signals
    """
    from app.db import SessionLocal
    from app.models import Bar, Signal
    from sqlalchemy import select, func
    
    async with SessionLocal() as session:
        # Count bars
        bar_count = await session.execute(select(func.count(Bar.id)))
        total_bars = bar_count.scalar()
        
        # Count signals
        signal_count = await session.execute(select(func.count(Signal.id)))
        total_signals = signal_count.scalar()
        
        # Get recent signals
        recent = await session.execute(
            select(Signal)
            .order_by(Signal.ts_signal.desc())
            .limit(5)
        )
        recent_signals = recent.scalars().all()
        
        return {
            "total_bars": total_bars,
            "total_signals": total_signals,
            "recent_signals": [
                {
                    "symbol": s.symbol,
                    "type": s.signal_type,
                    "indicator": s.indicator,
                    "price": s.price_at_signal,
                    "timestamp": s.ts_signal.isoformat()
                }
                for s in recent_signals
            ]
        }


@app.websocket("/ws/signals")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time signal streaming.
    
    Clients connect here to receive live divergence signals.
    """
    await websocket.accept()
    active_connections.append(websocket)
    
    logger.info(f"WebSocket connected. Total: {len(active_connections)}")
    
    try:
        # Keep connection alive
        while True:
            # Wait for any message (just to keep connection alive)
            await websocket.receive_text()
            
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(active_connections)}")