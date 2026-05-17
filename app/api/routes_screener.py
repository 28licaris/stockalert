"""
Screener HTTP routes — dashboard + script-facing.

Thin adapter over `Screener.scan`. One endpoint:
  POST /api/screener/scan  with a `ScreenerSpec` body
    -> `ScreenerResult`

Same service backs the MCP `scan_universe` tool. One contract,
two surfaces.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import APIRouter, Body, Depends, HTTPException

from app.services.screener.schemas import ScreenerResult, ScreenerSpec
from app.services.screener.screener import Screener

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache(maxsize=1)
def _build_screener() -> Screener:
    return Screener.from_settings()


def get_screener() -> Screener:
    """FastAPI dependency provider — override in tests."""
    return _build_screener()


@router.post("/screener/scan", response_model=ScreenerResult)
def post_screener_scan(
    spec: ScreenerSpec = Body(...),
    screener: Screener = Depends(get_screener),
) -> ScreenerResult:
    """Scan a universe with a declarative spec.

    Body: `ScreenerSpec` — see `app/services/screener/schemas.py`
    for the full schema.

    Returns: `ScreenerResult` — ranked candidates + diagnostics
    (universe size, n passed, per-symbol errors).

    Errors:
      - 422: spec validation (FastAPI default Pydantic error).
      - 400: unknown rule kind, missing rule params.
      - 500: infra error reading bars.
    """
    try:
        return screener.scan(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("screener/scan failed")
        raise HTTPException(
            status_code=500,
            detail=f"screener scan failed: {type(exc).__name__}: {exc}",
        ) from exc
