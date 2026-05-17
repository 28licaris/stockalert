"""
Unit tests for the live-tier and provider read services.

These are *unit* tests — they exercise the readers with stubbed CH
queries and stubbed providers, no real ClickHouse or HTTP calls.
Integration coverage (real CH, real Schwab REST) lives in the route
tests + manual smoke procedures documented in BUILD_JOURNAL.md.

What we cover:

  - BarReader: interval routing (direct vs resampled), unknown-interval
    ValueError, naive-datetime UTC coercion, ASC sort on get_recent_bars
    (which wraps a DESC query).
  - SignalReader: shape conversion + symbol-scoped variant.
  - QuoteService: field-alias normalization across Schwab / Polygon
    payload shapes, epoch-ms vs ISO timestamps, missing get_quotes
    method, invalidSymbols passthrough.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from app.services.readers.bar_reader import BarReader, _SUPPORTED_INTERVALS, _row_to_live_bar
from app.services.readers.signal_reader import SignalReader, _row_to_signal
from app.services.readers.quote_service import (
    QuoteService,
    _normalize_quote,
    _pick_numeric,
    _FIELD_ALIASES,
)
from app.services.readers.schemas import LiveBar, Signal, Quote, QuotesResponse


# ─────────────────────────────────────────────────────────────────────
# BarReader
# ─────────────────────────────────────────────────────────────────────


def _ch_row(ts_minute: int, **overrides) -> dict:
    return {
        "symbol": "AAPL",
        "timestamp": datetime(2024, 8, 1, 14, ts_minute, tzinfo=timezone.utc),
        "open": 100.0 + ts_minute * 0.01,
        "high": 100.5 + ts_minute * 0.01,
        "low": 99.5 + ts_minute * 0.01,
        "close": 100.2 + ts_minute * 0.01,
        "volume": 1000.0 + ts_minute,
        "vwap": 100.1 + ts_minute * 0.01,
        "trade_count": 10 + ts_minute,
        "source": "polygon",
        **overrides,
    }


def test_bar_reader_get_recent_bars_reverses_to_asc() -> None:
    """list_bars_desc returns DESC; reader must flip to ASC."""
    desc_rows = [_ch_row(m) for m in (10, 9, 8, 7, 6)]
    with patch("app.db.queries.list_bars_desc", return_value=desc_rows):
        bars = BarReader().get_recent_bars("AAPL", limit=5)

    assert len(bars) == 5
    assert [b.timestamp.minute for b in bars] == [6, 7, 8, 9, 10]
    assert all(isinstance(b, LiveBar) for b in bars)
    assert all(b.interval == "1m" for b in bars)


def test_bar_reader_get_recent_bars_zero_limit_skips_query() -> None:
    """limit=0 -> [] without hitting CH."""
    with patch("app.db.queries.list_bars_desc") as q:
        bars = BarReader().get_recent_bars("AAPL", limit=0)
    assert bars == []
    q.assert_not_called()


def test_bar_reader_unknown_interval_raises() -> None:
    """Bad interval is a programmer error."""
    with pytest.raises(ValueError, match="Unknown interval"):
        BarReader().get_bars_in_range(
            "AAPL",
            datetime(2024, 8, 1, tzinfo=timezone.utc),
            datetime(2024, 8, 2, tzinfo=timezone.utc),
            interval="2m",
        )


def test_bar_reader_inverted_window_returns_empty() -> None:
    """end <= start -> [] without hitting CH."""
    with patch("app.db.queries.fetch_bars") as q:
        bars = BarReader().get_bars_in_range(
            "AAPL",
            datetime(2024, 8, 2, tzinfo=timezone.utc),
            datetime(2024, 8, 1, tzinfo=timezone.utc),
            interval="1m",
        )
    assert bars == []
    q.assert_not_called()


def test_bar_reader_1m_uses_fetch_bars() -> None:
    """1m interval routes through queries.fetch_bars (DataFrame)."""
    df = pd.DataFrame([_ch_row(m) for m in range(3)])
    with patch("app.db.queries.fetch_bars", return_value=df) as q:
        bars = BarReader().get_bars_in_range(
            "AAPL",
            datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc),
            datetime(2024, 8, 1, 15, 0, tzinfo=timezone.utc),
            interval="1m",
        )
    q.assert_called_once()
    assert len(bars) == 3
    assert bars[0].interval == "1m"


def test_bar_reader_resampled_interval_uses_list_bars_resampled() -> None:
    """'15m' routes through queries.list_bars_resampled with interval kwarg."""
    rows = [_ch_row(m * 15) for m in range(2)]
    with patch("app.db.queries.list_bars_resampled", return_value=rows) as q:
        bars = BarReader().get_bars_in_range(
            "AAPL",
            datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc),
            datetime(2024, 8, 1, 16, 0, tzinfo=timezone.utc),
            interval="15m",
        )
    q.assert_called_once()
    kwargs = q.call_args.kwargs
    assert kwargs.get("interval") == "15m"
    assert all(b.interval == "15m" for b in bars)


def test_bar_reader_daily_uses_list_daily_bars() -> None:
    rows = [_ch_row(0, timestamp=datetime(2024, 8, d, tzinfo=timezone.utc)) for d in (1, 2)]
    with patch("app.db.queries.list_daily_bars", return_value=rows) as q:
        bars = BarReader().get_bars_in_range(
            "AAPL",
            datetime(2024, 8, 1, tzinfo=timezone.utc),
            datetime(2024, 8, 3, tzinfo=timezone.utc),
            interval="daily",
        )
    q.assert_called_once()
    assert len(bars) == 2
    assert bars[0].interval == "daily"


def test_bar_reader_get_bars_in_range_naive_datetime_coerced_to_utc() -> None:
    """Naive datetime is treated as UTC; reader doesn't raise."""
    df = pd.DataFrame([_ch_row(m) for m in range(2)])
    with patch("app.db.queries.fetch_bars", return_value=df):
        bars = BarReader().get_bars_in_range(
            "AAPL",
            datetime(2024, 8, 1, 14, 0),  # naive
            datetime(2024, 8, 1, 15, 0),  # naive
            interval="1m",
        )
    assert len(bars) == 2


def test_bar_reader_latest_bar_per_symbol_omits_missing() -> None:
    """Symbols with no CH row are omitted from the result."""
    rows = [_ch_row(30, symbol="AAPL"), _ch_row(31, symbol="MSFT")]
    with patch("app.db.queries.latest_bar_per_symbol", return_value=rows):
        out = BarReader().get_latest_bar_per_symbol(["AAPL", "MSFT", "GHOST"])
    assert set(out.keys()) == {"AAPL", "MSFT"}
    assert out["AAPL"].symbol == "AAPL"


def test_bar_reader_latest_bar_per_symbol_empty_input() -> None:
    """Empty input -> empty dict, no CH call."""
    with patch("app.db.queries.latest_bar_per_symbol") as q:
        assert BarReader().get_latest_bar_per_symbol([]) == {}
    q.assert_not_called()


def test_row_to_live_bar_handles_missing_vwap_and_trade_count() -> None:
    """vwap=0 -> None; trade_count=0 -> None; source missing -> None."""
    row = _ch_row(0)
    row["vwap"] = 0.0
    row["trade_count"] = 0
    row["source"] = ""
    bar = _row_to_live_bar(row, "1m")
    assert bar.vwap is None
    assert bar.trade_count is None
    assert bar.source is None


# ─────────────────────────────────────────────────────────────────────
# SignalReader
# ─────────────────────────────────────────────────────────────────────


def _signal_row(idx: int = 0) -> dict:
    return {
        "symbol": "AAPL",
        "signal_type": "hidden_bullish_divergence",
        "indicator": "rsi",
        "ts_signal": datetime(2024, 8, 1, 14, idx, tzinfo=timezone.utc),
        "price_at_signal": 100.0 + idx,
        "indicator_value": 30.0 + idx,
        "p1_ts": datetime(2024, 8, 1, 13, 50, tzinfo=timezone.utc),
        "p2_ts": datetime(2024, 8, 1, 14, idx, tzinfo=timezone.utc),
    }


def test_signal_reader_recent_signals() -> None:
    rows = [_signal_row(i) for i in range(3)]
    with patch("app.db.queries.recent_signals", return_value=rows) as q:
        signals = SignalReader().get_recent_signals(limit=10)
    q.assert_called_once_with(limit=10)
    assert len(signals) == 3
    assert all(isinstance(s, Signal) for s in signals)
    assert signals[0].symbol == "AAPL"
    assert signals[0].signal_type == "hidden_bullish_divergence"
    assert signals[0].indicator == "rsi"


def test_signal_reader_get_signals_by_symbol() -> None:
    rows = [_signal_row(i) for i in range(2)]
    with patch("app.db.queries.list_signals", return_value=rows) as q:
        signals = SignalReader().get_signals_by_symbol("AAPL", limit=50)
    q.assert_called_once_with("AAPL", 50)
    assert len(signals) == 2


def test_signal_reader_zero_limit_skips_query() -> None:
    with patch("app.db.queries.recent_signals") as q:
        assert SignalReader().get_recent_signals(limit=0) == []
    q.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# QuoteService
# ─────────────────────────────────────────────────────────────────────


def test_normalize_quote_schwab_shape() -> None:
    """Schwab keys: lastPrice / bidPrice / askPrice / quoteTime (epoch-ms)."""
    payload = {
        "lastPrice": 224.5,
        "bidPrice": 224.4,
        "askPrice": 224.6,
        "openPrice": 223.0,
        "highPrice": 225.0,
        "lowPrice": 222.5,
        "closePrice": 223.5,
        "totalVolume": 1234567,
        "quoteTime": 1700000000000,
    }
    q = _normalize_quote("AAPL", payload, "schwab")
    assert q.symbol == "AAPL"
    assert q.last == 224.5
    assert q.bid == 224.4
    assert q.ask == 224.6
    assert q.volume == 1234567.0
    assert q.provider == "schwab"
    assert q.timestamp is not None and q.timestamp.tzinfo is not None


def test_normalize_quote_wrapped_in_quote_key() -> None:
    """Some providers wrap fields under `quote: {...}` — service unwraps."""
    payload = {"quote": {"lastPrice": 100.5}}
    q = _normalize_quote("AAPL", payload, "schwab")
    assert q.last == 100.5


def test_normalize_quote_iso_timestamp() -> None:
    payload = {"lastPrice": 50.0, "quoteTime": "2024-08-01T14:30:00Z"}
    q = _normalize_quote("AAPL", payload, "polygon")
    assert q.timestamp is not None
    assert q.timestamp.hour == 14 and q.timestamp.minute == 30


def test_pick_numeric_falls_through_aliases() -> None:
    """First non-null alias wins."""
    assert _pick_numeric({"lastPrice": None, "last": 99.0}, _FIELD_ALIASES["last"]) == 99.0
    assert _pick_numeric({}, _FIELD_ALIASES["last"]) is None


class _FakeProvider:
    """Minimal stub for QuoteService provider."""

    def __init__(self, response: dict | None = None, raises: Exception | None = None) -> None:
        self._response = response or {}
        self._raises = raises
        self.called_with: list | None = None

    async def get_quotes(self, symbols):
        self.called_with = list(symbols)
        if self._raises:
            raise self._raises
        return self._response


def test_quote_service_get_quotes_normalizes_payload() -> None:
    provider = _FakeProvider(response={
        "AAPL": {"lastPrice": 224.5, "totalVolume": 1_000_000},
        "MSFT": {"lastPrice": 430.0, "totalVolume": 500_000},
        "errors": {"invalidSymbols": ["BOGUS"]},
    })
    svc = QuoteService(provider)

    resp = asyncio.run(svc.get_quotes(["AAPL", "MSFT", "BOGUS"]))

    assert resp.count == 2
    assert set(resp.quotes.keys()) == {"AAPL", "MSFT"}
    assert resp.quotes["AAPL"].last == 224.5
    assert resp.invalid_symbols == ["BOGUS"]
    assert provider.called_with == ["AAPL", "MSFT", "BOGUS"]


def test_quote_service_get_quotes_empty_input_skips_provider() -> None:
    provider = _FakeProvider(response={"AAPL": {"lastPrice": 1.0}})
    svc = QuoteService(provider)

    resp = asyncio.run(svc.get_quotes([]))
    assert resp.count == 0
    assert resp.invalid_symbols == []
    assert provider.called_with is None  # provider not consulted


def test_quote_service_provider_lacking_get_quotes_returns_empty() -> None:
    class _Bare:  # no get_quotes attr
        pass

    svc = QuoteService(_Bare())
    resp = asyncio.run(svc.get_quotes(["AAPL"]))
    assert resp.count == 0
    assert resp.invalid_symbols == ["AAPL"]


def test_quote_service_get_quote_single() -> None:
    provider = _FakeProvider(response={"AAPL": {"lastPrice": 224.5}})
    svc = QuoteService(provider)
    q = asyncio.run(svc.get_quote("AAPL"))
    assert q is not None and q.last == 224.5

    missing = asyncio.run(svc.get_quote("UNKNOWN"))
    # _FakeProvider returns the same response for any call; UNKNOWN wasn't in it
    # so this returns None.
    assert missing is None


def test_quote_service_provider_name_from_class() -> None:
    """`from_settings` chooses provider name from class — verify the heuristic."""
    class SchwabProvider:
        async def get_quotes(self, syms):
            return {}

    svc = QuoteService(SchwabProvider())
    assert svc._provider_name == "schwab"

    class WeirdName:
        async def get_quotes(self, syms):
            return {}

    svc2 = QuoteService(WeirdName())
    assert svc2._provider_name == "weirdname"
