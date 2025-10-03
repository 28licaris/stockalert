from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
import logging

from app.services.monitor_manager import monitor_manager

logger = logging.getLogger(__name__)

router = APIRouter()


class MonitorRequest(BaseModel):
    tickers: List[str]
    indicator: str = "rsi"
    signal_type: str = "hidden_bullish_divergence"


@router.get("/monitors")
async def list_monitors():
    """
    List all active monitors.
    
    FIXED: Made async and added error handling.
    """
    try:
        monitors = monitor_manager.list_monitors()
        return monitors
    except Exception as e:
        logger.error(f"Error listing monitors: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/monitors/start")
async def start_monitor(request: MonitorRequest):
    """Start monitoring specified symbols."""
    try:
        # Get broadcast callback from app state
        from fastapi import Request
        from starlette.requests import Request as StarletteRequest
        
        # Note: We can't easily access app.state here without dependency injection
        # For now, pass None and add WebSocket support later
        result = monitor_manager.start_monitor(
            tickers=request.tickers,
            indicator=request.indicator,
            signal_type=request.signal_type,
            broadcast_cb=None  # TODO: Add WebSocket broadcast
        )
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message"))
        
        return {
            "status": "success",
            "message": f"Monitor started for {', '.join(request.tickers)}",
            "details": result
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting monitor: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/monitors/stop")
async def stop_monitor(request: MonitorRequest):
    """Stop monitoring specified symbols."""
    try:
        result = monitor_manager.stop_monitor(
            tickers=request.tickers,
            indicator=request.indicator,
            signal_type=request.signal_type
        )
        
        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail="Monitor not found")
        
        return {
            "status": "success",
            "message": f"Monitor stopped for {', '.join(request.tickers)}",
            "details": result
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping monitor: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))