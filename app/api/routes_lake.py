"""
Lake (Iceberg bronze) read routes — the CH-independent surface.

These routes are the **first agent-facing read surface** that does not
depend on ClickHouse. Historical data for ML training, backtesting,
and LLM-agent context flows through here. The same `BronzeReader`
service backs the future MCP tools (Pre-Phase 3 Step 3), so the
Pydantic response shape is the contract both surfaces share.

Design rules (see `feedback_platform_design_intent` memory):

  - Route handlers contain ZERO business logic. They wrap a service
    method, convert exceptions to HTTP status codes, and return the
    service's response object.
  - `BronzeReader` is injected via FastAPI `Depends`. Tests override
    via `app.dependency_overrides[get_bronze_reader] = ...`.
  - No CH imports anywhere in the call path. The CH-independence
    claim is preserved by construction — if you find yourself adding
    `from app.db import ...` here, stop and re-think.
"""
from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.readers.bronze_reader import BronzeReader
from app.services.readers.schemas import BronzeBarsResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache(maxsize=1)
def _build_reader() -> BronzeReader:
    """
    Memoized reader instance. The underlying catalog is already
    `@lru_cache`'d in `iceberg_catalog.get_catalog()`, but caching the
    reader itself avoids re-running `from_settings()` on every request.
    """
    return BronzeReader.from_settings()


def get_bronze_reader() -> BronzeReader:
    """FastAPI dependency provider — override in tests."""
    return _build_reader()


@router.get("/lake/bars", response_model=BronzeBarsResponse)
def get_lake_bars(
    symbol: str = Query(..., description="Ticker symbol, e.g. 'AAPL'."),
    start: datetime = Query(
        ...,
        description=(
            "Window start (inclusive, ISO 8601). Naive datetimes treated as UTC."
        ),
    ),
    end: datetime = Query(
        ...,
        description=(
            "Window end (exclusive, ISO 8601). Naive datetimes treated as UTC."
        ),
    ),
    provider: str = Query(
        "polygon",
        description="Bronze provider table to read from. 'polygon' or 'schwab'.",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=50_000,
        description=(
            "If set, return at most this many bars (the MOST RECENT N "
            "from within the window). Useful for capping payload size "
            "when an agent asks for a wide range."
        ),
    ),
    reader: BronzeReader = Depends(get_bronze_reader),
) -> BronzeBarsResponse:
    """
    Return bronze minute bars for `symbol` in the half-open window
    `[start, end)` from the named provider's bronze Iceberg table.

    **Does not touch ClickHouse.** This route works when CH is stopped,
    redeployed, or wiped — that's intentional and load-bearing for
    ML-reproducibility and agent-readiness.

    Response shape matches `BronzeBarsResponse`: the request echo
    (symbol/start/end/provider) plus `bars: list[BronzeBar]` and
    `count: int`. MCP tools will return identical shape.

    Status codes:
      - 200 — query succeeded; `bars` may be empty if no rows in window.
      - 400 — unknown provider (programmer error / bad client input).
      - 500 — infra failure reading from Iceberg / S3 / Glue.
    """
    try:
        bars = reader.get_bars(
            symbol, start, end, provider=provider, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — boundary; convert to 500
        logger.exception("lake.get_bars failed for %s [%s..%s]", symbol, start, end)
        raise HTTPException(
            status_code=500,
            detail=f"bronze read failed: {type(exc).__name__}: {exc}",
        ) from exc

    return BronzeBarsResponse(
        symbol=symbol,
        start=start,
        end=end,
        provider=provider,
        bars=bars,
        count=len(bars),
    )
