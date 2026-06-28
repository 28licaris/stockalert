"""
News feed API — official-record items (SEC EDGAR filings; govt releases later),
AI-summarized with a link to the source. Backed by CH `news_items` via
``app.services.news.reader``. Powers the frontend News feed + the symbol News
tab. See docs/news_alerts_spec.md.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.news.reader import read_news
from app.services.news.schemas import NewsItem

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_LIMIT = 500
_ET = ZoneInfo("America/New_York")


class NewsDigest(BaseModel):
    date: date
    count: int
    items: list[NewsItem]


def _csv(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    items = [tok.strip() for tok in value.split(",") if tok.strip()]
    return items or None


@router.get("/news", response_model=list[NewsItem])
def get_news(
    symbols: Optional[str] = Query(
        None, description="Comma-separated tickers; market-wide items always included"
    ),
    types: Optional[str] = Query(
        None, description="Comma-separated event types, e.g. '8-K,4'"
    ),
    since: Optional[datetime] = Query(
        None, description="Only items published at/after this UTC timestamp"
    ),
    limit: int = Query(100, ge=1, le=_MAX_LIMIT),
) -> list[NewsItem]:
    """Most-recent news items, newest first. Unenriched items (``enriched=false``)
    appear with empty summary fields until the enrichment pass fills them."""
    try:
        return read_news(
            symbols=_csv(symbols),
            event_types=_csv(types),
            since=since,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("get_news failed (symbols=%s types=%s)", symbols, types)
        raise HTTPException(status_code=500, detail=f"news error: {exc}")


@router.get("/news/digest", response_model=NewsDigest)
def get_news_digest(
    day: Optional[date] = Query(
        None, alias="date", description="ET date; defaults to today (ET)"
    ),
    materiality: str = Query(
        "high", description="Comma-separated materiality levels to include"
    ),
    limit: int = Query(100, ge=1, le=_MAX_LIMIT),
) -> NewsDigest:
    """The day's material items (enriched only), newest first — a digest of what
    mattered. Window is one ET trading day; defaults to today ET."""
    et_day = day or datetime.now(_ET).date()
    start_et = datetime(et_day.year, et_day.month, et_day.day, tzinfo=_ET)
    since = start_et.astimezone(timezone.utc)
    until = (start_et + timedelta(days=1)).astimezone(timezone.utc)
    mats = _csv(materiality) or ["high"]
    try:
        items = read_news(
            materiality=mats, since=since, until=until,
            enriched_only=True, limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("get_news_digest failed (date=%s)", et_day)
        raise HTTPException(status_code=500, detail=f"news digest error: {exc}")
    return NewsDigest(date=et_day, count=len(items), items=items)
