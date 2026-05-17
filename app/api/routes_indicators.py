"""
Indicator HTTP routes — dashboard + script-facing.

Thin adapters over `IndicatorReader`. Two endpoints:

  GET  /api/indicators/series      — single indicator, single series.
  POST /api/indicators/chart-data  — multi-indicator + OHLCV bars in
                                     one bundle (the dashboard path).

Full design: `docs/indicator_exposure_design.md` §4.4.

Same `IndicatorReader` instance backs the MCP tools — see
`app/mcp/tools/indicators.py`. One service, two surfaces; identical
math, identical responses.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.readers.indicator_reader import IndicatorReader
from app.services.readers.schemas import IndicatorChartData, IndicatorSeries

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache(maxsize=1)
def _build_reader() -> IndicatorReader:
    return IndicatorReader.from_settings()


def get_indicator_reader() -> IndicatorReader:
    """FastAPI dependency provider — override in tests."""
    return _build_reader()


# ─────────────────────────────────────────────────────────────────────
# Request shapes (kept module-local; not part of the cross-surface
# contract that schemas.py owns).
# ─────────────────────────────────────────────────────────────────────


class IndicatorSpecRequest(BaseModel):
    """One indicator request in a `chart-data` POST."""

    name: str = Field(..., description="Registry name: 'sma', 'rsi', 'bollinger', ...")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Indicator constructor kwargs (e.g. `{period: 20}` for SMA).",
    )
    label: Optional[str] = Field(
        None,
        description="Display label. Defaults to a sensible 'SMA(20)' form when omitted.",
    )


class ChartDataRequest(BaseModel):
    """Body of POST /api/indicators/chart-data."""

    symbol: str
    start: datetime
    end: datetime
    interval: str = Field("1d", description="'1m' | '5m' | '15m' | '30m' | '1h' | '4h' | '1d'.")
    provider: str = Field("polygon", description="Bronze provider (only used when interval='1m').")
    indicators: list[IndicatorSpecRequest]


# ─────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────


@router.get("/indicators/series", response_model=IndicatorSeries)
def get_indicator_series(
    symbol: str = Query(..., description="Ticker symbol, e.g. 'AAPL'."),
    start: datetime = Query(..., description="Window start, inclusive (ISO 8601, naive treated as UTC)."),
    end: datetime = Query(..., description="Window end, exclusive (ISO 8601)."),
    indicator: str = Query(..., description="Registry name: 'sma', 'rsi', 'bollinger', ..."),
    interval: str = Query("1d", description="Bar interval: '1m', '5m', '15m', '30m', '1h', '4h', '1d'."),
    provider: str = Query("polygon", description="Bronze provider (only used when interval='1m')."),
    params: Optional[str] = Query(
        None,
        description=(
            "JSON-encoded indicator constructor kwargs, e.g. "
            "`{\"period\":20}` for SMA. Empty / omitted means use "
            "the indicator's default constructor."
        ),
    ),
    reader: IndicatorReader = Depends(get_indicator_reader),
) -> IndicatorSeries:
    """Single indicator series for `symbol` over `[start, end)`.

    For multi-output indicators (Bollinger / Stochastic / MACD) this
    returns only the canonical single-output component (middle band /
    %K / MACD line). Use POST /api/indicators/chart-data to get all
    components in one response.

    Errors:
      - 400 on unknown indicator name or invalid params.
      - 500 on infra failure reading bars.
    """
    parsed_params = _parse_params(params)
    try:
        return reader.get_series(
            symbol=symbol, indicator=indicator, params=parsed_params,
            start=start, end=end,
            interval=interval, provider=provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("indicators/series failed for %s %s", symbol, indicator)
        raise HTTPException(
            status_code=500,
            detail=f"indicator read failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/indicators/chart-data", response_model=IndicatorChartData)
def post_chart_data(
    body: ChartDataRequest = Body(...),
    reader: IndicatorReader = Depends(get_indicator_reader),
) -> IndicatorChartData:
    """Bars + N indicator series in one response — the chart endpoint.

    Multi-output indicators (Bollinger / Stochastic / MACD) decompose
    into multiple `IndicatorSeries` entries in `series` — one per
    component (e.g. `bollinger_upper`, `bollinger_middle`,
    `bollinger_lower`, `bollinger_bandwidth`, `bollinger_percent_b`).

    Reading from bronze (`interval='1m'`) pins `snapshot_id` for
    reproducibility; CH-backed reads (other intervals) return
    `snapshot_id: null`.
    """
    try:
        return reader.get_chart_data(
            symbol=body.symbol,
            indicator_specs=[s.model_dump() for s in body.indicators],
            start=body.start, end=body.end,
            interval=body.interval, provider=body.provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("indicators/chart-data failed for %s", body.symbol)
        raise HTTPException(
            status_code=500,
            detail=f"indicator read failed: {type(exc).__name__}: {exc}",
        ) from exc


def _parse_params(params_raw: Optional[str]) -> dict[str, Any]:
    """Parse the `params` query-string JSON. Empty / None -> {}."""
    if not params_raw:
        return {}
    try:
        parsed = json.loads(params_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"`params` must be valid JSON: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=400,
            detail="`params` must be a JSON object (dict).",
        )
    return parsed
