"""
Market calendar API — sessions (open / closed / early-close) over a date
range for equities or futures, backed by ``app.services.market_calendar``
(exchange_calendars). Powers the frontend Calendar view.

Each day carries an ``events`` list (empty for now). The events layer (FOMC,
econ releases, earnings) is Phase 2 — see docs/market_calendar_spec.md — and
will populate this field with NO change to this contract or the frontend.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import market_calendar as mc

logger = logging.getLogger(__name__)

router = APIRouter()

# Guardrail: a calendar view never needs more than a couple years at once.
_MAX_SPAN_DAYS = 800


class CalendarDay(BaseModel):
    date: date
    status: Literal["open", "closed", "early_close"]
    early_close_et: Optional[str] = Field(
        None, description="ET close time 'HH:MM' on early-close days, else null"
    )
    reason: Optional[str] = Field(
        None, description="Holiday/weekend name on closed days, else null"
    )
    events: list[Any] = Field(
        default_factory=list,
        description="Calendar events for this day (Phase 2: FOMC, econ, earnings). Empty for now.",
    )


class CalendarResponse(BaseModel):
    asset_class: Literal["equities", "futures"]
    start: date
    end: date
    days: list[CalendarDay]


@router.get("/calendar", response_model=CalendarResponse)
def get_calendar(
    start: date = Query(..., description="Inclusive start date (YYYY-MM-DD)"),
    end: date = Query(..., description="Inclusive end date (YYYY-MM-DD)"),
    asset_class: Literal["equities", "futures"] = Query("equities"),
) -> CalendarResponse:
    """Per-day market status for ``[start, end]`` (inclusive).

    Every calendar day is returned (not just sessions) so a frontend grid can
    render closed days too. Sessions are computed on-demand from the calendar
    library — deterministic and cheap for month/year ranges.
    """
    if end < start:
        raise HTTPException(status_code=400, detail="end must be >= start")
    if (end - start).days > _MAX_SPAN_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"range too large: max {_MAX_SPAN_DAYS} days",
        )

    try:
        rows = mc.calendar_range(asset_class, start, end)
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("get_calendar failed for %s %s..%s", asset_class, start, end)
        raise HTTPException(status_code=500, detail=f"calendar error: {exc}")

    days = [
        CalendarDay(
            date=r["date"],
            status=r["status"],
            early_close_et=r["early_close_et"],
            reason=r["reason"],
            events=[],
        )
        for r in rows
    ]
    return CalendarResponse(asset_class=asset_class, start=start, end=end, days=days)
