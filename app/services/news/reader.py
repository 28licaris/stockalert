"""Read path for the news feed — CH `news_items` → list[NewsItem].

Parameterized (no SQL injection from symbol/type filters). A symbol filter keeps
market-wide items (symbol='') so macro events still show in a watchlist view.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from app.services.news.schemas import NewsItem

_COLS = [
    "id", "published_at", "source", "event_type", "symbol", "cik", "title",
    "url", "summary", "why_it_matters", "materiality", "sentiment", "enriched",
]

_MAX_LIMIT = 500


def read_news(
    *,
    symbols: Optional[Sequence[str]] = None,
    event_types: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    limit: int = 100,
    ch_client=None,
) -> list[NewsItem]:
    client = ch_client
    if client is None:
        from app.db.client import get_client
        client = get_client()

    where: list[str] = []
    params: dict = {}
    if symbols:
        params["syms"] = [s.upper() for s in symbols]
        # Keep market-wide (macro) items even when filtering by symbol.
        where.append("(symbol IN {syms:Array(String)} OR symbol = '')")
    if event_types:
        params["types"] = [str(t) for t in event_types]
        where.append("event_type IN {types:Array(String)}")
    if since is not None:
        params["since"] = since
        where.append("published_at >= {since:DateTime64(3)}")

    n = max(1, min(int(limit), _MAX_LIMIT))
    sql = "SELECT " + ", ".join(_COLS) + " FROM news_items FINAL"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY published_at DESC LIMIT {n}"

    result = client.query(sql, parameters=params)
    out: list[NewsItem] = []
    for row in result.result_rows:
        r = dict(zip(_COLS, row))
        out.append(NewsItem(
            id=r["id"],
            published_at=r["published_at"],
            source=r["source"],
            event_type=r["event_type"],
            symbol=r["symbol"],
            cik=r["cik"],
            title=r["title"],
            url=r["url"],
            summary=r["summary"],
            why_it_matters=r["why_it_matters"],
            materiality=r["materiality"],
            sentiment=r["sentiment"],
            enriched=bool(r["enriched"]),
        ))
    return out
