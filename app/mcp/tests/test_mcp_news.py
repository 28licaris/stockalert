"""MCP tool tests — app/mcp/tools/news.py (get_news)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.mcp.server import mcp, register_all_tools
from app.services.news.schemas import NewsItem

register_all_tools()


def _structured(result):
    # call_tool returns (content_blocks, structured_dict).
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    return result


def test_get_news_is_registered():
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert "get_news" in names


def test_get_news_returns_items(monkeypatch):
    item = NewsItem(
        id="acc-1", published_at=datetime(2026, 6, 27, 16, 0, tzinfo=timezone.utc),
        source="edgar", event_type="8-K", symbol="AAPL", title="8-K - Apple",
        url="https://sec.gov/x", summary="S", why_it_matters="W",
        materiality="high", sentiment="positive", enriched=True,
    )
    captured = {}

    def fake_read_news(*, symbols, event_types, limit):
        captured.update(symbols=symbols, event_types=event_types, limit=limit)
        return [item]

    monkeypatch.setattr("app.services.news.reader.read_news", fake_read_news)

    result = asyncio.run(mcp.call_tool("get_news", {"symbols": "AAPL,NVDA", "limit": 10}))
    data = _structured(result)
    assert data["items"][0]["symbol"] == "AAPL"
    assert data["items"][0]["enriched"] is True
    assert captured["symbols"] == ["AAPL", "NVDA"]   # CSV parsed
    assert captured["limit"] == 10


def test_get_news_degrades_on_error(monkeypatch):
    def boom(**_):
        raise RuntimeError("ch down")

    monkeypatch.setattr("app.services.news.reader.read_news", boom)
    result = asyncio.run(mcp.call_tool("get_news", {}))
    assert _structured(result) == {"items": []}
