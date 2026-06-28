"""
News feed API — official-record items (SEC EDGAR filings; govt releases later),
AI-summarized with a link to the source. Backed by CH `news_items` via
``app.services.news.reader``. Powers the frontend News feed + the symbol News
tab. See docs/news_alerts_spec.md.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.news.reader import read_news
from app.services.news.schemas import NewsItem

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_LIMIT = 500


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
