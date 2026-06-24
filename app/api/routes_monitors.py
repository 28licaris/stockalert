"""HTTP API for live signal monitors.

A "monitor" is a long-running task that watches one or more symbols
for divergence signals against a configured indicator. Lifecycle
(start / stop / list) lives here; the actual signal stream lands in
the live `signals` topic (FE-CONTRACTS-7 WebSocket multiplex).

Elliott Wave live scanner endpoints (`/monitors/wave/*`) mirror the same
lifecycle but are backed by IntradayWaveScanner instead of MonitorService.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.schemas.monitors import (
    MonitorActionResponse,
    MonitorInfo,
    MonitorRequest,
)
from app.services.live.monitor_manager import monitor_manager

logger = logging.getLogger(__name__)

router = APIRouter()


class WaveScanRequest(BaseModel):
    symbols: list[str]
    interval: str = "5m"
    min_probability: float = 0.6
    min_risk_reward: float = 2.0


@router.get("/monitors", response_model=dict[str, MonitorInfo])
async def list_monitors() -> dict:
    """List all active monitors, keyed by monitor identity.

    Wire shape: `{ "<indicator>:<symbol>:<signal_type>": MonitorInfo, ... }`.
    The bare-dict shape is preserved so the cockpit reads it as
    `Record<string, MonitorInfo>` without extra unwrapping.
    """
    try:
        return monitor_manager.list_monitors()
    except Exception as e:
        logger.error(f"Error listing monitors: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/monitors/start", response_model=MonitorActionResponse)
async def start_monitor(request: MonitorRequest) -> MonitorActionResponse:
    """Start monitoring specified symbols."""
    try:
        result = monitor_manager.start_monitor(
            tickers=request.tickers,
            indicator=request.indicator,
            signal_type=request.signal_type,
            broadcast_cb=None,  # TODO: Add WebSocket broadcast (FE-CONTRACTS-7)
        )

        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message"))

        return MonitorActionResponse(
            status="success",
            message=f"Monitor started for {', '.join(request.tickers)}",
            details=result,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting monitor: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/monitors/stop", response_model=MonitorActionResponse)
async def stop_monitor(request: MonitorRequest) -> MonitorActionResponse:
    """Stop monitoring specified symbols."""
    try:
        result = monitor_manager.stop_monitor(
            tickers=request.tickers,
            indicator=request.indicator,
            signal_type=request.signal_type,
        )

        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail="Monitor not found")

        return MonitorActionResponse(
            status="success",
            message=f"Monitor stopped for {', '.join(request.tickers)}",
            details=result,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping monitor: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Elliott Wave live scanner lifecycle ──────────────────────────────────────

@router.get("/monitors/wave")
async def list_wave_scanners() -> dict:
    """List active Elliott Wave live scanners."""
    return monitor_manager.list_wave_scanners()


@router.post("/monitors/wave/start", response_model=MonitorActionResponse)
async def start_wave_scanner(request: WaveScanRequest, req: Request) -> MonitorActionResponse:
    """Start a live Elliott Wave scanner for the given symbols and interval.

    Alerts fire through the WebSocket signal multiplex (same channel as
    divergence alerts) so the frontend needs no new subscription.
    """
    broadcast_cb = getattr(getattr(req, "app", None) and req.app.state, "broadcast_signal", None)
    result = monitor_manager.start_wave_scanner(
        symbols=request.symbols,
        interval=request.interval,
        broadcast_cb=broadcast_cb,
        min_probability=request.min_probability,
        min_risk_reward=request.min_risk_reward,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return MonitorActionResponse(
        status="success",
        message=f"Wave scanner started for {', '.join(request.symbols)} @ {request.interval}",
        details=result,
    )


@router.post("/monitors/wave/stop", response_model=MonitorActionResponse)
async def stop_wave_scanner(request: WaveScanRequest) -> MonitorActionResponse:
    """Stop a running Elliott Wave scanner."""
    result = monitor_manager.stop_wave_scanner(
        symbols=request.symbols,
        interval=request.interval,
    )
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Wave scanner not found")
    return MonitorActionResponse(
        status="success",
        message=f"Wave scanner stopped for {', '.join(request.symbols)}",
        details=result,
    )
