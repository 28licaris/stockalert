"""HTTP routes for options lake reads."""
from __future__ import annotations

import logging
from datetime import date, datetime
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.options.schemas import (
    GammaAggregationLevel,
    GammaExposureResponse,
    LatestGammaExposureResponse,
    LatestOptionContractsResponse,
    OptionContractsResponse,
    PutCall,
)
from app.services.readers.options_hot_reader import OptionsHotReader
from app.services.readers.options_reader import OptionsReader

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache(maxsize=1)
def _build_reader() -> OptionsReader:
    return OptionsReader.from_settings()


def get_options_reader() -> OptionsReader:
    """FastAPI dependency provider; tests override this."""
    return _build_reader()


@lru_cache(maxsize=1)
def _build_hot_reader() -> OptionsHotReader:
    return OptionsHotReader.from_settings()


def get_options_hot_reader() -> OptionsHotReader:
    """FastAPI dependency provider for ClickHouse hot-tier reads."""
    return _build_hot_reader()


@router.get("/options/contracts/latest", response_model=LatestOptionContractsResponse)
def get_latest_option_contracts(
    symbol: str = Query(..., description="Underlying symbol, e.g. AAPL."),
    expiration_date: Optional[date] = Query(
        None,
        description="Optional contract expiration filter, YYYY-MM-DD.",
    ),
    put_call: Optional[PutCall] = Query(
        None,
        description="Optional side filter: CALL or PUT.",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=50_000,
        description="Return at most N latest hot-tier contracts.",
    ),
    reader: OptionsHotReader = Depends(get_options_hot_reader),
) -> LatestOptionContractsResponse:
    try:
        return reader.get_latest_contracts(
            symbol,
            expiration_date=expiration_date,
            put_call=put_call,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("options.contracts.latest failed for %s", symbol)
        raise HTTPException(
            status_code=500,
            detail=f"latest options contract read failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/options/gex/latest", response_model=LatestGammaExposureResponse)
def get_latest_option_gamma_exposure(
    symbol: str = Query(..., description="Underlying symbol, e.g. AAPL."),
    aggregation_level: Optional[GammaAggregationLevel] = Query(
        None,
        description="Optional GEX level: total, strike, expiry, or strike_expiry.",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=50_000,
        description="Return at most N latest hot-tier GEX rows.",
    ),
    reader: OptionsHotReader = Depends(get_options_hot_reader),
) -> LatestGammaExposureResponse:
    try:
        return reader.get_latest_gamma_exposure(
            symbol,
            aggregation_level=aggregation_level,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("options.gex.latest failed for %s", symbol)
        raise HTTPException(
            status_code=500,
            detail=f"latest options GEX read failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/options/contracts", response_model=OptionContractsResponse)
def get_option_contracts(
    symbol: str = Query(..., description="Underlying symbol, e.g. AAPL."),
    start: datetime = Query(..., description="Snapshot window start, inclusive."),
    end: datetime = Query(..., description="Snapshot window end, exclusive."),
    expiration_date: Optional[date] = Query(
        None,
        description="Optional contract expiration filter, YYYY-MM-DD.",
    ),
    put_call: Optional[PutCall] = Query(
        None,
        description="Optional side filter: CALL or PUT.",
    ),
    snapshot_id: Optional[str] = Query(
        None,
        description="Optional Iceberg snapshot id for deterministic replay.",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=50_000,
        description="Return at most the most recent N contracts in the window.",
    ),
    reader: OptionsReader = Depends(get_options_reader),
) -> OptionContractsResponse:
    try:
        return reader.get_contracts(
            symbol,
            start,
            end,
            expiration_date=expiration_date,
            put_call=put_call,
            snapshot_id=snapshot_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception(
            "options.contracts failed for %s [%s..%s]",
            symbol,
            start,
            end,
        )
        raise HTTPException(
            status_code=500,
            detail=f"options contract read failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/options/gex", response_model=GammaExposureResponse)
def get_option_gamma_exposure(
    symbol: str = Query(..., description="Underlying symbol, e.g. AAPL."),
    start: datetime = Query(..., description="Snapshot window start, inclusive."),
    end: datetime = Query(..., description="Snapshot window end, exclusive."),
    aggregation_level: Optional[GammaAggregationLevel] = Query(
        None,
        description="Optional GEX level: total, strike, expiry, or strike_expiry.",
    ),
    snapshot_id: Optional[str] = Query(
        None,
        description="Optional Iceberg snapshot id for deterministic replay.",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=50_000,
        description="Return at most the most recent N GEX rows in the window.",
    ),
    reader: OptionsReader = Depends(get_options_reader),
) -> GammaExposureResponse:
    try:
        return reader.get_gamma_exposure(
            symbol,
            start,
            end,
            aggregation_level=aggregation_level,
            snapshot_id=snapshot_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception(
            "options.gex failed for %s [%s..%s]",
            symbol,
            start,
            end,
        )
        raise HTTPException(
            status_code=500,
            detail=f"options GEX read failed: {type(exc).__name__}: {exc}",
        ) from exc
