"""Elliott Wave HTTP routes — dashboard + agent-facing.

Thin adapters over `WaveReader`. Same reader backs the MCP tools
(`app/mcp/tools/wave.py`) — one service, two surfaces.

  GET /api/v1/wave/{symbol}          — current wave state (primary + secondary)
  GET /api/v1/wave/{symbol}/history  — labeled history (for the chart overlay)
"""
from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.services.readers.wave_reader import WaveReader, WaveStateResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache(maxsize=1)
def _build_reader() -> WaveReader:
    return WaveReader.from_settings()


def get_wave_reader() -> WaveReader:
    return _build_reader()


@router.get("/wave/{symbol}", response_model=WaveStateResponse, tags=["Elliott Wave"])
def get_wave_state(
    symbol: str,
    interval: str = Query("1d"),
    backend: str = Query("auto", pattern="^(store|compute|auto)$"),
    reader: WaveReader = Depends(get_wave_reader),
) -> WaveStateResponse:
    return reader.get_state(symbol, interval, backend=backend)  # type: ignore[arg-type]


@router.get("/wave/{symbol}/history", response_model=list[WaveStateResponse],
            tags=["Elliott Wave"])
def get_wave_history(
    symbol: str,
    interval: str = Query("1d"),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    reader: WaveReader = Depends(get_wave_reader),
) -> list[WaveStateResponse]:
    return reader.get_history(symbol, interval, start=start, end=end)
