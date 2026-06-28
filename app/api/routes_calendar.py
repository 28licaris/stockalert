"""
Market calendar API — sessions (open / closed / early-close) over a date
range for equities or futures, backed by ``app.services.market_calendar``
(exchange_calendars). Powers the frontend Calendar view.

Each day carries an ``events`` list — FOMC + OPEX/quad-witching today, with
dividend/split ex-dates and (later) earnings landing via the same contract.
See docs/market_calendar_spec.md §12a.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import market_calendar as mc
from app.services import market_events as me

logger = logging.getLogger(__name__)

router = APIRouter()

# Guardrail: a calendar view never needs more than a couple years at once.
_MAX_SPAN_DAYS = 800


class CalendarEvent(BaseModel):
    event_type: str = Field(description="fomc | opex | quad_witching | dividend | split | …")
    title: str
    importance: str = Field(description="low | medium | high")
    time_et: Optional[str] = Field(None, description="ET time 'HH:MM', if known")
    symbol: Optional[str] = Field(None, description="Ticker for company events; null for market-wide")
    source: str


class CalendarDay(BaseModel):
    date: date
    status: Literal["open", "closed", "early_close"]
    early_close_et: Optional[str] = Field(
        None, description="ET close time 'HH:MM' on early-close days, else null"
    )
    reason: Optional[str] = Field(
        None, description="Holiday/weekend name on closed days, else null"
    )
    events: list[CalendarEvent] = Field(
        default_factory=list, description="Calendar events on this day (FOMC, OPEX, …)."
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

    # Group events by date (events failing must NOT break the sessions grid).
    events_by_date: dict[date, list[CalendarEvent]] = {}
    try:
        for e in me.events_in_range(start, end):
            events_by_date.setdefault(e["event_date"], []).append(
                CalendarEvent(
                    event_type=e["event_type"],
                    title=e["title"],
                    importance=e["importance"],
                    time_et=e["event_time_et"] or None,
                    symbol=e["symbol"] or None,
                    source=e["source"],
                )
            )
    except Exception:  # noqa: BLE001 — boundary; sessions still render
        logger.exception("get_calendar: events lookup failed; returning sessions only")

    days = [
        CalendarDay(
            date=r["date"],
            status=r["status"],
            early_close_et=r["early_close_et"],
            reason=r["reason"],
            events=events_by_date.get(r["date"], []),
        )
        for r in rows
    ]
    return CalendarResponse(asset_class=asset_class, start=start, end=end, days=days)
