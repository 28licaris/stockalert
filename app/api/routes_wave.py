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

from app.services.alerts import WaveAlert, scan_alerts, scan_intraday_alerts
from app.services.alerts.intraday import INTRADAY_INTERVALS
from app.services.readers.wave_reader import WaveReader, WaveStateResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache(maxsize=1)
def _build_reader() -> WaveReader:
    return WaveReader.from_settings()


def get_wave_reader() -> WaveReader:
    return _build_reader()


@router.get("/wave/alerts", response_model=list[WaveAlert], tags=["Elliott Wave"])
def get_wave_alerts(
    interval: str = Query("1d"),
    min_probability: float = Query(0.6, ge=0, le=1),
    min_risk_reward: float = Query(2.0, ge=0),
    reader: WaveReader = Depends(get_wave_reader),
) -> list[WaveAlert]:
    return scan_alerts(interval, min_probability=min_probability,
                       min_risk_reward=min_risk_reward, reader=reader)


@router.get("/wave/alerts/intraday", response_model=list[WaveAlert], tags=["Elliott Wave"])
def get_intraday_wave_alerts(
    symbols: str = Query(..., description="Comma-separated symbols, e.g. AAPL,TSLA,/GC"),
    interval: str = Query("5m", pattern="^(1m|5m|15m|30m|1h)$"),
    min_probability: float = Query(0.6, ge=0, le=1),
    min_risk_reward: float = Query(2.0, ge=0),
) -> list[WaveAlert]:
    """EW-7: On-demand intraday wave alert scan.

    Bars are pulled from ClickHouse (hot cache) — sub-100ms per symbol.
    Returns alerts where the primary count is in an impulse wave 3 or 5,
    probability >= min_probability, and R:R >= min_risk_reward.
    """
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    return scan_intraday_alerts(sym_list, interval,
                                min_probability=min_probability,
                                min_risk_reward=min_risk_reward)


def _norm_symbol(symbol: str) -> str:
    """Futures roots are `/`-prefixed (e.g. /GC). The frontend sends them as a
    slash-preserving path (`/api/v1/wave//GC`), so a single leading slash here
    is intentional — collapse any accidental duplicates, keep one."""
    s = symbol.strip()
    return "/" + s.lstrip("/") if s.startswith("/") else s


# NOTE: `{symbol:path}` (not `{symbol}`) so futures roots like "/GC" route — a
# bare path param can't contain a slash. The /history route is declared before
# the bare one so the greedy `:path` doesn't swallow the "/history" suffix.
@router.get("/wave/{symbol:path}/history", response_model=list[WaveStateResponse],
            tags=["Elliott Wave"])
def get_wave_history(
    symbol: str,
    interval: str = Query("1d"),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    reader: WaveReader = Depends(get_wave_reader),
) -> list[WaveStateResponse]:
    return reader.get_history(_norm_symbol(symbol), interval, start=start, end=end)


@router.get("/wave/{symbol:path}", response_model=WaveStateResponse, tags=["Elliott Wave"])
def get_wave_state(
    symbol: str,
    interval: str = Query("1d"),
    backend: str = Query("auto", pattern="^(store|compute|auto)$"),
    reader: WaveReader = Depends(get_wave_reader),
) -> WaveStateResponse:
    return reader.get_state(_norm_symbol(symbol), interval, backend=backend)  # type: ignore[arg-type]
