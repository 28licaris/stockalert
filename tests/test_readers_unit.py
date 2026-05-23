"""
Unit tests for the live-tier and provider read services.

These are *unit* tests — they exercise the readers with stubbed CH
queries and stubbed providers, no real ClickHouse or HTTP calls.
Integration coverage (real CH, real Schwab REST) lives in the route
tests + tests/integration/ (gated by the `integration` marker).

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


def test_bar_reader_resampled_interval_passes_source_table() -> None:
    """source_table kwarg flows through to queries.list_bars_resampled."""
    rows = [
        {"ts": datetime(2024, 8, 1, 14, m * 15, tzinfo=timezone.utc),
         "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000}
        for m in range(2)
    ]
    with patch("app.db.queries.list_bars_resampled", return_value=rows) as q:
        bars = BarReader().get_bars_in_range(
            "AAPL",
            datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc),
            datetime(2024, 8, 1, 16, 0, tzinfo=timezone.utc),
            interval="15m",
            source_table="ohlcv_5m",
        )
    q.assert_called_once()
    assert q.call_args.kwargs.get("source_table") == "ohlcv_5m"
    assert all(b.interval == "15m" for b in bars)


def test_bar_reader_1d_uses_list_daily_bars() -> None:
    """`1d` interval uses queries.list_daily_bars (the native daily table)."""
    rows = [_ch_row(0, timestamp=datetime(2024, 8, d, tzinfo=timezone.utc)) for d in (1, 2)]
    with patch("app.db.queries.list_daily_bars", return_value=rows) as q:
        bars = BarReader().get_bars_in_range(
            "AAPL",
            datetime(2024, 8, 1, tzinfo=timezone.utc),
            datetime(2024, 8, 3, tzinfo=timezone.utc),
            interval="1d",
        )
    q.assert_called_once()
    assert len(bars) == 2
    assert bars[0].interval == "1d"


def test_bar_reader_resampled_row_with_ts_key_handled() -> None:
    """`list_bars_resampled` returns `ts`, not `timestamp`; reader copes."""
    resampled_rows = [
        {"ts": datetime(2024, 8, 1, 14, m, tzinfo=timezone.utc),
         "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2, "volume": 1234}
        for m in (0, 5, 10)
    ]
    with patch("app.db.queries.list_bars_resampled", return_value=resampled_rows):
        bars = BarReader().get_bars_in_range(
            "AAPL",
            datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc),
            datetime(2024, 8, 1, 15, 0, tzinfo=timezone.utc),
            interval="5m",
        )
    assert len(bars) == 3
    assert bars[0].timestamp.minute == 0
    assert bars[0].interval == "5m"
    # Resampled rows have no symbol/vwap/trade_count/source; reader defaults.
    assert bars[0].symbol == "AAPL"  # from the `symbol=` kwarg passthrough
    assert bars[0].vwap is None
    assert bars[0].trade_count is None


def test_bar_reader_1m_routes_through_list_bars_resampled() -> None:
    """`1m` goes through list_bars_resampled (pass-through aggregation)."""
    rows = [
        {"ts": datetime(2024, 8, 1, 14, m, tzinfo=timezone.utc),
         "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2, "volume": 1234}
        for m in range(3)
    ]
    with patch("app.db.queries.list_bars_resampled", return_value=rows) as q:
        bars = BarReader().get_bars_in_range(
            "AAPL",
            datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc),
            datetime(2024, 8, 1, 15, 0, tzinfo=timezone.utc),
            interval="1m",
        )
    q.assert_called_once()
    args = q.call_args.args
    assert args[1] == "1m"  # interval passed positionally
    assert len(bars) == 3


def test_bar_reader_get_bars_in_range_naive_datetime_coerced_to_utc() -> None:
    """Naive datetime is treated as UTC; reader doesn't raise."""
    rows = [
        {"ts": datetime(2024, 8, 1, 14, m, tzinfo=timezone.utc),
         "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000}
        for m in range(2)
    ]
    with patch("app.db.queries.list_bars_resampled", return_value=rows):
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


# ─────────────────────────────────────────────────────────────────────
# BarReader.get_bars_for_chart  (the multi-table-fallback helper)
# ─────────────────────────────────────────────────────────────────────


def _resampled_rows(n: int = 3) -> list[dict]:
    return [
        {"ts": datetime(2024, 8, 1, 14, m, tzinfo=timezone.utc),
         "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000}
        for m in range(n)
    ]


def test_get_bars_for_chart_1d_prefers_native_daily_table() -> None:
    """`1d` -> list_daily_bars first; resampled fallback NOT called when daily has rows."""
    daily_rows = [
        {"timestamp": datetime(2024, 8, d, tzinfo=timezone.utc),
         "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000}
        for d in (1, 2, 3)
    ]
    with patch("app.db.queries.list_daily_bars", return_value=daily_rows) as native, \
         patch("app.db.queries.list_bars_resampled") as resampled:
        bars = BarReader().get_bars_for_chart("AAPL", interval="1d", lookback_days=30)
    native.assert_called_once()
    resampled.assert_not_called()
    assert len(bars) == 3
    assert all(b.interval == "1d" for b in bars)


def test_get_bars_for_chart_1d_falls_back_to_resampled_when_daily_empty() -> None:
    """`1d` with empty daily table -> resampled fallback."""
    with patch("app.db.queries.list_daily_bars", return_value=[]) as native, \
         patch("app.db.queries.list_bars_resampled", return_value=_resampled_rows()) as resampled:
        bars = BarReader().get_bars_for_chart("AAPL", interval="1d", lookback_days=10)
    native.assert_called_once()
    resampled.assert_called_once()
    assert len(bars) == 3


def test_get_bars_for_chart_1m_always_uses_ohlcv_1m_source() -> None:
    with patch("app.db.queries.list_bars_resampled", return_value=_resampled_rows()) as q:
        BarReader().get_bars_for_chart("AAPL", interval="1m", lookback_days=5)
    assert q.call_args.kwargs.get("source_table") == "ohlcv_1m"


def test_get_bars_for_chart_5m_short_lookback_uses_ohlcv_1m() -> None:
    with patch("app.db.queries.list_bars_resampled", return_value=_resampled_rows()) as q:
        BarReader().get_bars_for_chart("AAPL", interval="5m", lookback_days=10)
    assert q.call_args.kwargs.get("source_table") == "ohlcv_1m"


def test_get_bars_for_chart_5m_long_lookback_prefers_ohlcv_5m() -> None:
    """lookback > 48 days -> source_table='ohlcv_5m'."""
    with patch("app.db.queries.list_bars_resampled", return_value=_resampled_rows()) as q:
        BarReader().get_bars_for_chart("AAPL", interval="5m", lookback_days=180)
    assert q.call_args.kwargs.get("source_table") == "ohlcv_5m"


def test_get_bars_for_chart_5m_long_lookback_falls_back_to_1m_when_5m_empty() -> None:
    """If ohlcv_5m is empty (first visit), retry against ohlcv_1m."""
    calls = []

    def fake_resampled(symbol, interval, start, end, limit, *, source_table="ohlcv_1m"):
        calls.append(source_table)
        if source_table == "ohlcv_5m":
            return []
        return _resampled_rows()

    with patch("app.db.queries.list_bars_resampled", side_effect=fake_resampled):
        bars = BarReader().get_bars_for_chart("AAPL", interval="5m", lookback_days=180)

    assert calls == ["ohlcv_5m", "ohlcv_1m"]
    assert len(bars) == 3


def test_get_bars_for_chart_auto_limit_scales_with_lookback() -> None:
    """No `limit` + `lookback_days` -> auto-sized limit pushed to query."""
    captured: dict = {}

    def fake_resampled(symbol, interval, start, end, limit, *, source_table="ohlcv_1m"):
        captured["limit"] = limit
        return _resampled_rows()

    with patch("app.db.queries.list_bars_resampled", side_effect=fake_resampled):
        BarReader().get_bars_for_chart("AAPL", interval="1h", lookback_days=100)

    # 1h has ~16 bars/day; 100 * 16 * 1.5 = 2400. min(500, 2400) -> 2400.
    assert captured["limit"] >= 1600
    assert captured["limit"] <= 100_000


def test_get_bars_for_chart_auto_limit_defaults_to_500_without_lookback() -> None:
    captured: dict = {}

    def fake_resampled(symbol, interval, start, end, limit, *, source_table="ohlcv_1m"):
        captured["limit"] = limit
        return []

    with patch("app.db.queries.list_bars_resampled", side_effect=fake_resampled):
        BarReader().get_bars_for_chart("AAPL", interval="1h")

    assert captured["limit"] == 500


def test_get_bars_for_chart_unknown_interval_raises() -> None:
    with pytest.raises(ValueError, match="Unknown interval"):
        BarReader().get_bars_for_chart("AAPL", interval="2m", lookback_days=5)


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
    """Mirrors the short-key shape `queries.list_signals` actually returns."""
    return {
        "id": f"00000000-0000-0000-0000-{idx:012d}",
        "symbol": "AAPL",
        "type": "hidden_bullish_divergence",
        "indicator": "rsi",
        "ts": datetime(2024, 8, 1, 14, idx, tzinfo=timezone.utc),
        "price": 100.0 + idx,
        "indicator_value": 30.0 + idx,
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
    """Minimal stub for QuoteService provider — records every call."""

    def __init__(self, response: dict | None = None, raises: Exception | None = None) -> None:
        self._response = response or {}
        self._raises = raises
        self.calls: list[list[str]] = []

    @property
    def called_with(self) -> list | None:
        """Back-compat: last batch we were called with."""
        return self.calls[-1] if self.calls else None

    async def get_quotes(self, symbols):
        self.calls.append(list(symbols))
        if self._raises:
            raise self._raises
        # Return only quotes for the requested chunk (so chunking tests
        # see one row per requested symbol in each call).
        out: dict = {}
        for s in symbols:
            if s in self._response:
                out[s] = self._response[s]
        if "errors" in self._response:
            out["errors"] = self._response["errors"]
        return out


class _PerChunkProvider:
    """Provider that returns a different payload per chunk — for chunking tests."""

    def __init__(self) -> None:
        self.chunk_calls: list[list[str]] = []

    async def get_quotes(self, symbols):
        self.chunk_calls.append(list(symbols))
        return {s: {"lastPrice": float(i + 1)} for i, s in enumerate(symbols)}


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


def test_quote_service_chunks_large_batches() -> None:
    """A 55-symbol request with default chunk size 25 -> 3 chunks (25+25+5)."""
    provider = _PerChunkProvider()
    svc = QuoteService(provider)

    symbols = [f"S{i:03d}" for i in range(55)]
    resp = asyncio.run(svc.get_quotes(symbols))

    # 3 chunks total.
    assert len(provider.chunk_calls) == 3
    assert [len(c) for c in provider.chunk_calls] == [25, 25, 5]
    # All 55 quotes returned merged.
    assert resp.count == 55
    assert all(f"S{i:03d}" in resp.quotes for i in range(55))


def test_quote_service_custom_chunk_size() -> None:
    """Caller can override chunk_size."""
    provider = _PerChunkProvider()
    svc = QuoteService(provider)

    asyncio.run(svc.get_quotes([f"S{i}" for i in range(7)], chunk_size=3))

    assert [len(c) for c in provider.chunk_calls] == [3, 3, 1]


def test_quote_service_continues_when_one_chunk_fails() -> None:
    """A failing chunk logs + continues; other chunks still return quotes."""
    call_count = [0]

    class _FlakyProvider:
        async def get_quotes(self, symbols):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("transient network blip")
            return {s: {"lastPrice": 1.0} for s in symbols}

    svc = QuoteService(_FlakyProvider())
    resp = asyncio.run(svc.get_quotes(
        [f"S{i:03d}" for i in range(75)], chunk_size=25
    ))

    # Three chunks attempted; second raised; we accumulate the other two.
    assert call_count[0] == 3
    assert resp.count == 50  # chunks 1 and 3 succeeded


def test_quote_service_chunk_size_does_not_affect_response_count() -> None:
    """chunk_size only affects how many provider calls happen, not the result."""
    provider = _PerChunkProvider()
    svc = QuoteService(provider)
    resp = asyncio.run(svc.get_quotes(["AAPL", "MSFT", "NVDA"], chunk_size=1))
    assert resp.count == 3
    assert len(provider.chunk_calls) == 3  # one symbol per chunk


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
