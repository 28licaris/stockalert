"""Unit tests for app.services.news.reader.read_news — fake CH, no network."""
from __future__ import annotations

from datetime import datetime, timezone

from app.services.news.reader import _COLS, read_news


class _FakeQR:
    def __init__(self, rows):
        self.result_rows = rows


class _FakeCH:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.last_sql = None
        self.last_params = None

    def query(self, sql, parameters=None):
        self.last_sql = sql
        self.last_params = parameters
        return _FakeQR(self.rows)


def _row(symbol="AAPL", enriched=1):
    now = datetime(2026, 6, 27, 16, 0, tzinfo=timezone.utc)
    return [
        "acc-1", now, "edgar", "8-K", symbol, "320193", "8-K - Apple",
        "https://sec.gov/x", "Summary.", "Why.", "high", "positive", enriched,
    ]


def test_maps_rows_to_news_items():
    ch = _FakeCH([_row(enriched=1), _row(symbol="", enriched=0)])
    items = read_news(ch_client=ch)
    assert len(items) == 2
    assert items[0].symbol == "AAPL"
    assert items[0].materiality == "high"
    assert items[0].enriched is True
    assert items[1].enriched is False    # UInt8 0 → False


def test_symbol_filter_keeps_market_wide():
    ch = _FakeCH([])
    read_news(symbols=["aapl", "nvda"], ch_client=ch)
    assert "symbol IN {syms:Array(String)}" in ch.last_sql
    assert "OR symbol = ''" in ch.last_sql        # macro still included
    assert ch.last_params["syms"] == ["AAPL", "NVDA"]   # upper-cased


def test_event_type_and_since_params():
    ch = _FakeCH([])
    since = datetime(2026, 6, 1, tzinfo=timezone.utc)
    read_news(event_types=["8-K", "4"], since=since, ch_client=ch)
    assert "event_type IN {types:Array(String)}" in ch.last_sql
    assert ch.last_params["types"] == ["8-K", "4"]
    assert "published_at >= {since:DateTime64(3)}" in ch.last_sql
    assert ch.last_params["since"] == since


def test_limit_is_clamped():
    ch = _FakeCH([])
    read_news(limit=10_000, ch_client=ch)
    assert "LIMIT 500" in ch.last_sql
    read_news(limit=0, ch_client=ch)
    assert "LIMIT 1" in ch.last_sql
