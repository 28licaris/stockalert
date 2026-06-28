"""
Economic indicators API — latest figures + history for free government series
(BLS now; BEA later). Backed by CH `economic_data` via
``app.services.news.econ``. Powers the cockpit Economic page + the AI.
See docs/news_alerts_spec.md §14.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.services.news.econ import (
    BLS_SERIES,
    EconHistoryPoint,
    EconIndicator,
    EconService,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/economic", response_model=list[EconIndicator])
def get_economic() -> list[EconIndicator]:
    """Latest figure + change for each tracked indicator (CPI, jobs, …)."""
    try:
        return EconService.from_settings().latest()
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("get_economic failed")
        raise HTTPException(status_code=500, detail=f"economic error: {exc}")


@router.get("/economic/{series_id}/history", response_model=list[EconHistoryPoint])
def get_economic_history(
    series_id: str,
    limit: int = Query(60, ge=1, le=600),
) -> list[EconHistoryPoint]:
    """Raw release history (newest first) for one indicator."""
    if series_id not in BLS_SERIES:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    try:
        return EconService.from_settings().history(series_id, limit=limit)
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("get_economic_history failed (%s)", series_id)
        raise HTTPException(status_code=500, detail=f"economic error: {exc}")
