"""
Unit tests for PolygonProvider (REST historical path).

These tests do NOT hit the Polygon API. The synchronous `list_aggs` call is
patched on a per-test basis so we can validate the timeframe mapping, the
DataFrame shape contract, and error handling without network access.

Run with: poetry run pytest tests/test_polygon_provider.py -v
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.providers.polygon_provider import PolygonProvider, _timeframe_to_polygon


def _fake_agg(ts_ms: int, *, o=1.0, h=2.0, l=0.5, c=1.5, v=100, vw=1.25, n=10):
    """
    Build a `massive.rest.models.Agg`-compatible duck. The real `Agg` is just
    a @modelclass dataclass with optional fields, so a SimpleNamespace works
    everywhere PolygonProvider reads attributes.
    """
    return SimpleNamespace(
        timestamp=ts_ms,
        open=o, high=h, low=l, close=c,
        volume=v, vwap=vw, transactions=n,
    )


class TestTimeframeMapping:
    """Pure mapping function — no provider state involved."""

    @pytest.mark.parametrize("tf,expected", [
        ("1Min", (1, "minute")),
        ("1m", (1, "minute")),
        ("5Min", (5, "minute")),
        ("15m", (15, "minute")),
        ("30Min", (30, "minute")),
        ("1h", (1, "hour")),
        ("1d", (1, "day")),
        ("daily", (1, "day")),
        ("1w", (1, "week")),
        ("1mo", (1, "month")),
    ])
    def test_known_timeframes(self, tf, expected):
        assert _timeframe_to_polygon(tf) == expected

    def test_unknown_falls_back_to_1min(self):
        # SchwabProvider has the same fallback so any caller passing a weird
        # value gets minute bars instead of an error.
        assert _timeframe_to_polygon("banana") == (1, "minute")


class TestHistoricalDf:
    """REST historical path with mocked `list_aggs`."""

    @pytest.fixture
    def provider(self) -> PolygonProvider:
        return PolygonProvider(api_key="test-key")

    @pytest.fixture
    def window(self) -> tuple[datetime, datetime]:
        end = datetime(2025, 5, 13, 20, 0, tzinfo=timezone.utc)
        return end - timedelta(days=1), end

    def test_returns_empty_df_for_empty_symbol(self, provider, window):
        start, end = window
        df = asyncio.run(provider.historical_df("", start, end, "1Min"))
        assert df.empty

    def test_returns_empty_df_when_provider_returns_no_aggs(self, provider, window):
        start, end = window
        with patch.object(provider, "_fetch_aggs_sync", return_value=[]):
            df = asyncio.run(provider.historical_df("AAPL", start, end, "1Min"))
        assert df.empty

    def test_dataframe_shape_and_index(self, provider, window):
        start, end = window
        # Two consecutive 1-min bars at 14:30 and 14:31 UTC on 2025-05-13.
        aggs = [
            _fake_agg(1747146600000, o=200.0, h=200.5, l=199.5, c=200.25,
                      v=4930, vw=200.13, n=129),
            _fake_agg(1747146660000, o=200.25, h=200.4, l=200.1, c=200.3,
                      v=1815, vw=200.27, n=57),
        ]
        with patch.object(provider, "_fetch_aggs_sync", return_value=aggs):
            df = asyncio.run(provider.historical_df("AAPL", start, end, "1Min"))

        assert list(df.columns) == ["open", "high", "low", "close", "volume", "vwap", "trade_count"]
        assert df.index.name == "timestamp"
        # Index must be UTC-aware to match the rest of the provider contract.
        assert df.index.tz is not None
        # Sorted ascending so downstream consumers can rely on it.
        assert df.index.is_monotonic_increasing
        assert len(df) == 2
        assert float(df.iloc[0]["open"]) == 200.0
        assert int(df.iloc[0]["trade_count"]) == 129
        assert float(df.iloc[1]["vwap"]) == 200.27

    def test_sorts_unordered_results(self, provider, window):
        """Polygon should return ascending, but we guard against any drift."""
        start, end = window
        aggs = [
            _fake_agg(1747146660000, o=2.0, h=2.0, l=2.0, c=2.0, v=10),
            _fake_agg(1747146600000, o=1.0, h=1.0, l=1.0, c=1.0, v=10),
        ]
        with patch.object(provider, "_fetch_aggs_sync", return_value=aggs):
            df = asyncio.run(provider.historical_df("AAPL", start, end, "1Min"))
        assert df.index.is_monotonic_increasing
        assert float(df.iloc[0]["open"]) == 1.0

    def test_skips_aggs_with_no_timestamp(self, provider, window):
        start, end = window
        aggs = [
            _fake_agg(1747146600000, o=1.0),
            SimpleNamespace(timestamp=None, open=2.0, high=2.0, low=2.0,
                            close=2.0, volume=0, vwap=0, transactions=0),
            _fake_agg(1747146660000, o=3.0),
        ]
        with patch.object(provider, "_fetch_aggs_sync", return_value=aggs):
            df = asyncio.run(provider.historical_df("AAPL", start, end, "1Min"))
        assert len(df) == 2

    def test_provider_error_returns_empty_df(self, provider, window):
        """Errors from the SDK must not crash callers; mirrors Schwab/Alpaca."""
        start, end = window
        with patch.object(provider, "_fetch_aggs_sync",
                          side_effect=RuntimeError("boom")):
            df = asyncio.run(provider.historical_df("AAPL", start, end, "1Min"))
        assert df.empty

    def test_naive_datetimes_are_treated_as_utc(self, provider):
        start = datetime(2025, 5, 12, 0, 0)  # naive
        end = datetime(2025, 5, 13, 0, 0)    # naive

        captured: dict = {}

        def fake_fetch(ticker, multiplier, timespan, s, e):
            captured["start_tz"] = s.tzinfo
            captured["end_tz"] = e.tzinfo
            return []

        with patch.object(provider, "_fetch_aggs_sync", side_effect=fake_fetch):
            df = asyncio.run(provider.historical_df("AAPL", start, end, "1Min"))
        assert df.empty
        assert captured["start_tz"] is timezone.utc
        assert captured["end_tz"] is timezone.utc

    def test_passes_correct_timeframe_to_client(self, provider, window):
        """5m timeframe must map through to (5, 'minute') at the SDK boundary."""
        start, end = window
        captured: dict = {}

        def fake_fetch(ticker, multiplier, timespan, s, e):
            captured["multiplier"] = multiplier
            captured["timespan"] = timespan
            captured["ticker"] = ticker
            return []

        with patch.object(provider, "_fetch_aggs_sync", side_effect=fake_fetch):
            asyncio.run(provider.historical_df("spy", start, end, "5Min"))

        assert captured["multiplier"] == 5
        assert captured["timespan"] == "minute"
        # Symbol should be normalized to uppercase before reaching the SDK.
        assert captured["ticker"] == "SPY"


class TestAggToBar:
    """EquityAgg -> SimpleNamespace bar mapping. Pure function."""

    def test_maps_required_fields(self):
        msg = SimpleNamespace(
            event_type="AM", symbol="AAPL",
            open=200.0, high=200.5, low=199.5, close=200.25,
            volume=1234, vwap=200.1,
            start_timestamp=1747146600000,  # 2025-05-13 14:30:00 UTC
            end_timestamp=1747146660000,
        )
        bar = PolygonProvider._agg_to_bar(msg)
        assert bar is not None
        assert bar.symbol == "AAPL" and bar.ticker == "AAPL"
        assert bar.open == 200.0 and bar.high == 200.5
        assert bar.low == 199.5 and bar.close == 200.25
        assert bar.volume == 1234.0
        assert bar.vwap == 200.1
        # The bar timestamp tracks the start of the minute so it lines up with
        # SchwabProvider field 7 (ChartTime) semantics.
        assert bar.timestamp.tzinfo is timezone.utc
        assert int(bar.timestamp.timestamp() * 1000) == 1747146600000
        assert bar.ts == bar.timestamp

    def test_returns_none_for_empty_symbol(self):
        msg = SimpleNamespace(event_type="AM", symbol="", open=1.0)
        assert PolygonProvider._agg_to_bar(msg) is None

    def test_falls_back_to_end_timestamp(self):
        msg = SimpleNamespace(event_type="AM", symbol="MSFT",
                              start_timestamp=None,
                              end_timestamp=1747146660000)
        bar = PolygonProvider._agg_to_bar(msg)
        assert bar is not None
        assert int(bar.timestamp.timestamp() * 1000) == 1747146660000

    def test_missing_timestamps_use_now(self):
        msg = SimpleNamespace(event_type="AM", symbol="SPY",
                              start_timestamp=None, end_timestamp=None)
        bar = PolygonProvider._agg_to_bar(msg)
        assert bar is not None
        assert bar.timestamp.tzinfo is timezone.utc

    def test_normalizes_symbol_to_upper(self):
        msg = SimpleNamespace(event_type="AM", symbol="aapl",
                              start_timestamp=1747146600000)
        bar = PolygonProvider._agg_to_bar(msg)
        assert bar.symbol == "AAPL"


class TestOnMessages:
    """Verify the WebSocket processor filters and dispatches correctly."""

    def _make_provider_with_main_loop(self):
        """
        Build a provider with a real running event loop attached. We collect
        bars via an async callback and inspect the cross-thread dispatch path.
        """
        provider = PolygonProvider(api_key="test")
        loop = asyncio.new_event_loop()
        provider._main_loop = loop
        received: list = []

        async def cb(bar):
            received.append(bar)

        provider._bar_callback = cb
        return provider, loop, received

    def test_drops_messages_when_no_callback(self):
        """Defensive: should not raise if subscribe_bars hasn't wired a callback yet."""
        provider = PolygonProvider(api_key="test")
        provider._main_loop = asyncio.new_event_loop()
        try:
            msg = SimpleNamespace(event_type="AM", symbol="AAPL",
                                  start_timestamp=1747146600000)
            asyncio.run(provider._on_messages([msg]))  # must not raise
        finally:
            provider._main_loop.close()

    def test_filters_non_am_events(self):
        """A trade message (ev='T') must be ignored, not dispatched."""
        provider, loop, received = self._make_provider_with_main_loop()
        try:
            loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
            loop_thread.start()
            try:
                msgs = [
                    SimpleNamespace(event_type="T", symbol="AAPL"),
                    SimpleNamespace(event_type="Q", symbol="AAPL"),
                    SimpleNamespace(event_type="AM", symbol="AAPL",
                                    open=1, high=1, low=1, close=1, volume=1,
                                    start_timestamp=1747146600000),
                ]
                asyncio.run(provider._on_messages(msgs))
                # Give the main loop a moment to drain the scheduled coroutine.
                _wait_until(lambda: len(received) == 1, timeout=2.0)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                loop_thread.join(timeout=2.0)
        finally:
            loop.close()
        assert len(received) == 1
        assert received[0].symbol == "AAPL"

    def test_handles_enum_event_type(self):
        """`event_type` may arrive as an Enum from the SDK; treat .value as the string."""
        provider, loop, received = self._make_provider_with_main_loop()
        try:
            loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
            loop_thread.start()
            try:
                enum_ev = SimpleNamespace(value="AM")  # duck-typed Enum
                msg = SimpleNamespace(event_type=enum_ev, symbol="MSFT",
                                      open=1, high=1, low=1, close=1, volume=1,
                                      start_timestamp=1747146600000)
                asyncio.run(provider._on_messages([msg]))
                _wait_until(lambda: len(received) == 1, timeout=2.0)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                loop_thread.join(timeout=2.0)
        finally:
            loop.close()
        assert received[0].symbol == "MSFT"


class TestSubscribeBars:
    """End-to-end subscribe/unsubscribe wiring without actually opening a socket."""

    def _patch_ws_client(self, provider):
        """Replace `_ws_client()` with a MagicMock so no real WebSocketClient is built."""
        fake_ws = MagicMock()
        fake_ws.subscribe = MagicMock()
        fake_ws.unsubscribe = MagicMock()
        provider._ws = fake_ws
        # Avoid spinning up the daemon thread: stub `start_stream` so the test
        # focuses on subscription bookkeeping. A separate test exercises the
        # streamer thread lifecycle.
        provider.start_stream = MagicMock(  # type: ignore[assignment]
            side_effect=lambda: setattr(provider, "_streamer_started", True)
        )
        return fake_ws

    def test_subscribe_bars_requires_running_loop(self):
        provider = PolygonProvider(api_key="test")
        provider.subscribe_bars(lambda _: None, ["AAPL"])  # not awaited; no loop
        assert provider._main_loop is None
        assert provider._subscribed_tickers == []

    def test_subscribe_bars_pushes_AM_prefix_and_starts_stream(self):
        provider = PolygonProvider(api_key="test")
        fake_ws = self._patch_ws_client(provider)

        async def run():
            await asyncio.sleep(0)  # ensure a running loop
            provider.subscribe_bars(lambda _: None, ["aapl", "MSFT"])

        asyncio.run(run())

        fake_ws.subscribe.assert_any_call("AM.AAPL")
        fake_ws.subscribe.assert_any_call("AM.MSFT")
        assert provider._subscribed_tickers == ["AAPL", "MSFT"]
        assert provider._streamer_started is True

    def test_subscribe_bars_deduplicates(self):
        provider = PolygonProvider(api_key="test")
        fake_ws = self._patch_ws_client(provider)

        async def run():
            await asyncio.sleep(0)
            provider.subscribe_bars(lambda _: None, ["AAPL"])
            provider.subscribe_bars(lambda _: None, ["AAPL", "GOOG"])

        asyncio.run(run())
        assert provider._subscribed_tickers == ["AAPL", "GOOG"]
        # AAPL must only be sent once even across two calls (the second sub
        # should only contain the newly added GOOG).
        sub_calls = [c.args[0] for c in fake_ws.subscribe.call_args_list]
        assert sub_calls.count("AM.AAPL") == 1
        assert "AM.GOOG" in sub_calls

    def test_unsubscribe_removes_ticker_and_sends_unsub(self):
        provider = PolygonProvider(api_key="test")
        fake_ws = self._patch_ws_client(provider)

        async def run():
            await asyncio.sleep(0)
            provider.subscribe_bars(lambda _: None, ["AAPL", "MSFT"])

        asyncio.run(run())
        provider.unsubscribe_bars(["aapl"])

        fake_ws.unsubscribe.assert_called_with("AM.AAPL")
        assert provider._subscribed_tickers == ["MSFT"]


class TestStartStopStream:
    """Streamer thread lifecycle with the websocket fully mocked."""

    def test_start_stream_is_idempotent(self):
        provider = PolygonProvider(api_key="test")
        # Replace the websocket client builder with one that never opens a real
        # socket. `connect` is awaited inside the thread's event loop.
        fake_ws = MagicMock()
        async def fake_connect(processor):
            # Sit until the loop is told to stop.
            while True:
                await asyncio.sleep(0.05)
        fake_ws.connect = fake_connect
        async def fake_close():
            return None
        fake_ws.close = fake_close
        provider._ws = fake_ws

        provider.start_stream()
        first_thread = provider._streamer_thread
        provider.start_stream()  # second call must be a no-op
        assert provider._streamer_thread is first_thread

        # Cleanup: stop the thread so the test process doesn't leak.
        provider.stop_stream()

    def test_stop_stream_safe_when_not_started(self):
        provider = PolygonProvider(api_key="test")
        # Must not raise even though no thread was ever started.
        assert provider.stop_stream() is None
        assert provider.unsubscribe_bars(["AAPL"]) is None


def _stock_snap(
    ticker: str,
    *,
    last_price: float | None = None,
    prev_close: float | None = None,
    todays_change: float | None = None,
    todays_pct: float | None = None,
    day_volume: float | None = None,
    day_trades: int | None = None,
    day_close: float | None = None,
) -> SimpleNamespace:
    """Build a TickerSnapshot duck for stock snapshot tests."""
    last_trade = SimpleNamespace(price=last_price) if last_price is not None else None
    prev_day = SimpleNamespace(close=prev_close) if prev_close is not None else None
    day = SimpleNamespace(
        close=day_close if day_close is not None else last_price,
        volume=day_volume,
        transactions=day_trades,
    )
    return SimpleNamespace(
        ticker=ticker,
        last_trade=last_trade,
        prev_day=prev_day,
        day=day,
        todays_change=todays_change,
        todays_change_percent=todays_pct,
    )


def _index_snap(
    ticker: str,
    *,
    value: float | None = None,
    prev_close: float | None = None,
    change: float | None = None,
    change_pct: float | None = None,
    name: str = "",
) -> SimpleNamespace:
    """Build an IndicesSnapshot duck for index snapshot tests."""
    session = SimpleNamespace(
        previous_close=prev_close, change=change, change_percent=change_pct,
    )
    return SimpleNamespace(ticker=ticker, value=value, session=session, name=name)


class TestTranslateSymbolForPolygon:
    """Symbol routing: caller's vocab -> Polygon ticker + asset class bucket."""

    @pytest.mark.parametrize("inp,want", [
        ("AAPL",  ("AAPL",  "stocks")),
        ("aapl",  ("AAPL",  "stocks")),
        ("$SPX",  ("I:SPX", "indices")),
        ("I:VIX", ("I:VIX", "indices")),
        ("O:AAPL", ("O:AAPL", "options")),
        ("X:BTCUSD", ("X:BTCUSD", "fx_crypto")),
        ("/ESM26", ("",      "unsupported")),
        ("",      ("",      "unsupported")),
        ("   ",   ("",      "unsupported")),
    ])
    def test_translation(self, inp, want):
        assert PolygonProvider._translate_symbol_for_polygon(inp) == want


class TestGetQuotes:
    """get_quotes builds the Schwab-shaped tape response from Polygon snapshots."""

    def _provider(self) -> PolygonProvider:
        return PolygonProvider(api_key="test")

    def test_empty_input_returns_empty(self):
        p = self._provider()
        assert asyncio.run(p.get_quotes([])) == {}

    def test_stock_block_shape(self):
        p = self._provider()
        snap = _stock_snap("SPY", last_price=600.0, prev_close=595.0,
                           todays_change=5.0, todays_pct=0.84,
                           day_volume=12_345_678, day_trades=4321)
        with patch.object(p, "_fetch_stock_snapshots_sync", return_value=[snap]):
            out = asyncio.run(p.get_quotes(["SPY"]))

        assert "SPY" in out and "errors" not in out
        block = out["SPY"]
        assert block["assetMainType"] == "EQUITY"
        q = block["quote"]
        assert q["lastPrice"] == 600.0
        assert q["closePrice"] == 595.0
        assert q["netChange"] == 5.0
        assert q["netPercentChange"] == 0.84

    def test_recomputes_missing_change_fields(self):
        """If Polygon omits todays_change, derive it from last + prev_close."""
        p = self._provider()
        snap = _stock_snap("AAPL", last_price=210.0, prev_close=200.0,
                           todays_change=None, todays_pct=None)
        with patch.object(p, "_fetch_stock_snapshots_sync", return_value=[snap]):
            out = asyncio.run(p.get_quotes(["AAPL"]))
        q = out["AAPL"]["quote"]
        assert q["netChange"] == pytest.approx(10.0)
        assert q["netPercentChange"] == pytest.approx(5.0)

    def test_translates_dollar_index_to_I_prefix(self):
        p = self._provider()
        captured: dict = {}

        def fake_idx(tickers: list[str]) -> list:
            captured["tickers"] = list(tickers)
            return [_index_snap("I:SPX", value=5300.5, prev_close=5280.0,
                                change=20.5, change_pct=0.39, name="S&P 500")]

        with patch.object(p, "_fetch_index_snapshots_sync", side_effect=fake_idx):
            out = asyncio.run(p.get_quotes(["$SPX"]))

        assert captured["tickers"] == ["I:SPX"]
        assert "$SPX" in out  # original caller key preserved
        block = out["$SPX"]
        assert block["assetMainType"] == "INDEX"
        assert block["quote"]["lastPrice"] == 5300.5
        assert block["quote"]["closePrice"] == 5280.0
        assert block["quote"]["netChange"] == 20.5
        assert block["quote"]["netPercentChange"] == 0.39

    def test_unsupported_symbols_go_to_invalid(self):
        """Futures (Schwab '/' prefix) aren't routable via Polygon snapshots."""
        p = self._provider()
        with patch.object(p, "_fetch_stock_snapshots_sync", return_value=[]):
            out = asyncio.run(p.get_quotes(["/ESM26"]))
        assert out == {"errors": {"invalidSymbols": ["/ESM26"]}}

    def test_unknown_symbol_goes_to_invalid(self):
        p = self._provider()
        # Polygon returns nothing for a bogus ticker -> caller key recorded as invalid.
        with patch.object(p, "_fetch_stock_snapshots_sync", return_value=[]):
            out = asyncio.run(p.get_quotes(["ZZZZ"]))
        assert "ZZZZ" not in out
        assert out.get("errors", {}).get("invalidSymbols") == ["ZZZZ"]

    def test_mixed_bucket_split_and_merge(self):
        """Stocks + indices in one call should each go to their own endpoint."""
        p = self._provider()
        with patch.object(
            p, "_fetch_stock_snapshots_sync",
            return_value=[_stock_snap("AAPL", last_price=210.0, prev_close=200.0,
                                       todays_change=10.0, todays_pct=5.0)],
        ), patch.object(
            p, "_fetch_index_snapshots_sync",
            return_value=[_index_snap("I:SPX", value=5300.5, prev_close=5280.0,
                                      change=20.5, change_pct=0.39)],
        ):
            out = asyncio.run(p.get_quotes(["AAPL", "$SPX"]))
        assert set(out.keys()) == {"AAPL", "$SPX"}
        assert out["AAPL"]["assetMainType"] == "EQUITY"
        assert out["$SPX"]["assetMainType"] == "INDEX"

    def test_provider_error_collects_invalid_without_raising(self):
        p = self._provider()
        with patch.object(p, "_fetch_stock_snapshots_sync",
                          side_effect=RuntimeError("boom")):
            out = asyncio.run(p.get_quotes(["AAPL"]))
        assert "AAPL" not in out
        assert "AAPL" in (out.get("errors") or {}).get("invalidSymbols", [])


class TestGetMovers:
    """get_movers wraps Polygon's gainers/losers snapshot for the Schwab route."""

    def _provider(self) -> PolygonProvider:
        return PolygonProvider(api_key="test")

    def test_market_wide_flag_is_true(self):
        """The route relies on this attribute to avoid 5x fan-out per /movers call."""
        assert PolygonProvider.MOVERS_MARKET_WIDE is True

    def test_percent_change_up_calls_gainers(self):
        p = self._provider()
        called: list[str] = []

        def fake_dir(direction: str):
            called.append(direction)
            return [_stock_snap("AAPL", last_price=210.0, prev_close=200.0,
                                 todays_change=10.0, todays_pct=5.0,
                                 day_volume=1_000_000)]

        with patch.object(p, "_fetch_direction_sync", side_effect=fake_dir):
            out = asyncio.run(p.get_movers(sort="PERCENT_CHANGE_UP"))
        assert called == ["gainers"]
        screeners = out["screeners"]
        assert len(screeners) == 1
        row = screeners[0]
        assert row["symbol"] == "AAPL"
        assert row["lastPrice"] == 210.0
        assert row["netPercentChange"] == 5.0
        assert row["direction"] == "up"
        assert row["totalVolume"] == 1_000_000.0

    def test_percent_change_down_calls_losers(self):
        p = self._provider()
        called: list[str] = []

        def fake_dir(direction: str):
            called.append(direction)
            return [_stock_snap("BAD", last_price=90.0, prev_close=100.0,
                                 todays_change=-10.0, todays_pct=-10.0)]

        with patch.object(p, "_fetch_direction_sync", side_effect=fake_dir):
            out = asyncio.run(p.get_movers(sort="PERCENT_CHANGE_DOWN"))
        assert called == ["losers"]
        row = out["screeners"][0]
        assert row["symbol"] == "BAD"
        assert row["netPercentChange"] == -10.0
        assert row["direction"] == "down"

    def test_volume_sort_fetches_both_and_dedupes(self):
        """VOLUME/TRADES want a wider pool; we hit both directions and dedupe."""
        p = self._provider()
        called: list[str] = []

        def fake_dir(direction: str):
            called.append(direction)
            if direction == "gainers":
                return [_stock_snap("AAPL", last_price=210.0, prev_close=200.0,
                                     todays_change=10.0, todays_pct=5.0,
                                     day_volume=1_000_000)]
            return [
                _stock_snap("AAPL", last_price=210.0, prev_close=200.0,
                             todays_change=10.0, todays_pct=5.0),  # duplicate sym
                _stock_snap("BAD", last_price=90.0, prev_close=100.0,
                             todays_change=-10.0, todays_pct=-10.0,
                             day_volume=900_000),
            ]

        with patch.object(p, "_fetch_direction_sync", side_effect=fake_dir):
            out = asyncio.run(p.get_movers(sort="VOLUME"))
        assert called == ["gainers", "losers"]
        symbols = [r["symbol"] for r in out["screeners"]]
        assert symbols == ["AAPL", "BAD"]  # dedupe keeps first-seen

    def test_ignores_symbol_id_argument(self):
        """Polygon's endpoint is market-wide; callers may still pass `$SPX` from
        the Schwab vocabulary and we must accept it as a no-op."""
        p = self._provider()
        with patch.object(p, "_fetch_direction_sync", return_value=[]):
            out = asyncio.run(p.get_movers("$SPX", sort="PERCENT_CHANGE_UP"))
        assert out == {"screeners": []}

    def test_direction_error_does_not_raise(self):
        p = self._provider()
        with patch.object(p, "_fetch_direction_sync",
                          side_effect=RuntimeError("boom")):
            out = asyncio.run(p.get_movers(sort="VOLUME"))
        # Both directions errored; we still return an empty `screeners` list
        # rather than raising, mirroring the rest of the provider contract.
        assert out == {"screeners": []}

    def test_get_movers_enriches_description_from_name_cache(self):
        """Movers rows must surface the company name (NAME column on the dashboard)."""
        p = self._provider()
        snap = _stock_snap("AAPL", last_price=210.0, prev_close=200.0,
                           todays_change=10.0, todays_pct=5.0)
        with patch.object(p, "_fetch_direction_sync", return_value=[snap]), \
             patch.object(p, "_fetch_ticker_name_sync", return_value="Apple Inc."):
            out = asyncio.run(p.get_movers(sort="PERCENT_CHANGE_UP"))
        assert out["screeners"][0]["description"] == "Apple Inc."

    def test_get_movers_tolerates_name_lookup_failure(self):
        """Reference API down? Still return movers, just without descriptions."""
        p = self._provider()
        snap = _stock_snap("AAPL", last_price=210.0, prev_close=200.0,
                           todays_change=10.0, todays_pct=5.0)
        with patch.object(p, "_fetch_direction_sync", return_value=[snap]), \
             patch.object(p, "_fetch_ticker_name_sync",
                          side_effect=RuntimeError("boom")):
            out = asyncio.run(p.get_movers(sort="PERCENT_CHANGE_UP"))
        # Row is present, description stays None/falsy.
        assert out["screeners"][0]["symbol"] == "AAPL"
        assert not out["screeners"][0].get("description")


class TestLookupTickerNames:
    """Process-wide cache for /v3/reference/tickers lookups."""

    def test_empty_input_returns_empty(self):
        p = PolygonProvider(api_key="test")
        assert asyncio.run(p._lookup_ticker_names([])) == {}

    def test_caches_results_across_calls(self):
        """Second call for the same ticker must NOT hit the SDK again."""
        p = PolygonProvider(api_key="test")
        with patch.object(p, "_fetch_ticker_name_sync",
                          return_value="Apple Inc.") as fetch:
            first = asyncio.run(p._lookup_ticker_names(["AAPL"]))
            second = asyncio.run(p._lookup_ticker_names(["AAPL"]))
        assert first == {"AAPL": "Apple Inc."}
        assert second == {"AAPL": "Apple Inc."}
        assert fetch.call_count == 1

    def test_caches_negative_lookups(self):
        """Tickers that returned no name shouldn't be retried on every refresh."""
        p = PolygonProvider(api_key="test")
        with patch.object(p, "_fetch_ticker_name_sync",
                          return_value=None) as fetch:
            asyncio.run(p._lookup_ticker_names(["ZZZZ"]))
            asyncio.run(p._lookup_ticker_names(["ZZZZ"]))
        assert fetch.call_count == 1
        assert "ZZZZ" in p._ticker_unknown_names

    def test_returns_only_known_names(self):
        p = PolygonProvider(api_key="test")

        def fake_fetch(t: str):
            return "Apple Inc." if t == "AAPL" else None

        with patch.object(p, "_fetch_ticker_name_sync", side_effect=fake_fetch):
            out = asyncio.run(p._lookup_ticker_names(["AAPL", "ZZZZ"]))
        # Missing tickers are absent from the dict (callers treat as "no name").
        assert out == {"AAPL": "Apple Inc."}

    def test_dedupes_input(self):
        p = PolygonProvider(api_key="test")
        with patch.object(p, "_fetch_ticker_name_sync",
                          return_value="Apple Inc.") as fetch:
            asyncio.run(p._lookup_ticker_names(["AAPL", "AAPL", "AAPL"]))
        assert fetch.call_count == 1

    def test_get_quotes_populates_description_via_cache(self):
        """Banner items should get their company name from the cache."""
        p = PolygonProvider(api_key="test")
        snap = _stock_snap("SPY", last_price=600.0, prev_close=595.0,
                           todays_change=5.0, todays_pct=0.84)
        with patch.object(p, "_fetch_stock_snapshots_sync", return_value=[snap]), \
             patch.object(p, "_fetch_ticker_name_sync",
                          return_value="SPDR S&P 500 ETF Trust"):
            out = asyncio.run(p.get_quotes(["SPY"]))
        assert out["SPY"]["reference"]["description"] == "SPDR S&P 500 ETF Trust"


def _ref_ticker(symbol: str, name: str, *, type_: str = "CS",
                exchange: str = "XNAS") -> SimpleNamespace:
    """Polygon `Ticker` reference object duck-typed for search tests."""
    return SimpleNamespace(
        ticker=symbol,
        name=name,
        type=type_,
        primary_exchange=exchange,
        active=True,
    )


class TestSearchInstruments:
    """Autocomplete via Polygon's /v3/reference/tickers."""

    def test_empty_query_short_circuits(self):
        p = PolygonProvider(api_key="test")
        # Should NOT make any network call.
        with patch.object(p, "_search_tickers_sync") as fetch:
            assert asyncio.run(p.search_instruments("")) == []
            assert asyncio.run(p.search_instruments("   ")) == []
            assert asyncio.run(p.search_instruments("AAPL", limit=0)) == []
        fetch.assert_not_called()

    def test_normalizes_polygon_ticker_to_search_shape(self):
        """Polygon `Ticker` → `{symbol, description, exchange, asset_type}`."""
        p = PolygonProvider(api_key="test")
        raw = [
            _ref_ticker("AAPL", "Apple Inc.", type_="CS", exchange="XNAS"),
            _ref_ticker("SPY",  "SPDR S&P 500 ETF Trust", type_="ETF", exchange="ARCX"),
        ]
        with patch.object(p, "_search_tickers_sync", return_value=raw):
            out = asyncio.run(p.search_instruments("AAPL", limit=5))
        # AAPL (exact match) leads; both have human-friendly types/exchanges.
        assert out[0] == {
            "symbol": "AAPL",
            "description": "Apple Inc.",
            "exchange": "NASDAQ",
            "asset_type": "EQUITY",
        }
        assert out[1] == {
            "symbol": "SPY",
            "description": "SPDR S&P 500 ETF Trust",
            "exchange": "NYSE ARCA",
            "asset_type": "ETF",
        }

    def test_exact_ticker_match_floats_to_top(self):
        """Even when relevance pass ranks decoys first, an exact ticker wins."""
        p = PolygonProvider(api_key="test")
        # Mock the live SDK output: a bunch of *QQQ* ETFs come first, with
        # the actual QQQ ETF buried mid-list.
        raw = [
            _ref_ticker("DVQQ", "WEBs QQQ Defined Volatility ETF", type_="ETF"),
            _ref_ticker("PSQ",  "ProShares Short QQQ", type_="ETF"),
            _ref_ticker("QBIG", "Invesco Top QQQ ETF", type_="ETF"),
            _ref_ticker("QQQ",  "Invesco QQQ Trust", type_="ETF"),
            _ref_ticker("QEW",  "Invesco QQQ Equal Weight ETF", type_="ETF"),
        ]
        with patch.object(p, "_search_tickers_sync", return_value=raw):
            out = asyncio.run(p.search_instruments("QQQ", limit=5))
        assert out[0]["symbol"] == "QQQ"
        # Description-substring matches still appear, just behind the exact hit.
        assert {r["symbol"] for r in out} >= {"QQQ", "DVQQ", "PSQ", "QBIG", "QEW"}

    def test_equity_beats_warrant_on_ties(self):
        """Asset-type bias keeps EQUITY/ETF above WARRANT/PFD for the same root."""
        p = PolygonProvider(api_key="test")
        raw = [
            _ref_ticker("FOOW", "Foo Corp Warrant", type_="WARRANT"),
            _ref_ticker("FOO",  "Foo Corp Common Stock", type_="CS"),
        ]
        with patch.object(p, "_search_tickers_sync", return_value=raw):
            out = asyncio.run(p.search_instruments("foo", limit=5))
        # FOO (EQUITY, length 3) ranks above FOOW (WARRANT, length 4).
        symbols = [r["symbol"] for r in out]
        assert symbols.index("FOO") < symbols.index("FOOW")

    def test_dedupes_overlap_between_exact_and_fuzzy_passes(self):
        """If the exact and fuzzy passes both return AAPL, only one row emerges."""
        p = PolygonProvider(api_key="test")
        # Same symbol appears twice (first from `ticker=` pass, second from
        # `search=` pass). The merged result must contain it once.
        raw = [
            _ref_ticker("AAPL", "Apple Inc.", type_="CS"),
            _ref_ticker("AAPL", "Apple Inc.", type_="CS"),
            _ref_ticker("AAPB", "GraniteShares 2x Long AAPL", type_="ETF"),
        ]
        with patch.object(p, "_search_tickers_sync", return_value=raw):
            out = asyncio.run(p.search_instruments("AAPL", limit=5))
        symbols = [r["symbol"] for r in out]
        assert symbols.count("AAPL") == 1
        assert "AAPB" in symbols

    def test_respects_limit(self):
        p = PolygonProvider(api_key="test")
        raw = [_ref_ticker(f"SYM{i}", f"Company {i}") for i in range(20)]
        with patch.object(p, "_search_tickers_sync", return_value=raw):
            out = asyncio.run(p.search_instruments("sym", limit=3))
        assert len(out) == 3

    def test_returns_empty_on_provider_error(self):
        """SDK errors must NOT bubble up — autocomplete failure is silent."""
        p = PolygonProvider(api_key="test")
        with patch.object(p, "_search_tickers_sync",
                          side_effect=RuntimeError("boom")):
            out = asyncio.run(p.search_instruments("AAPL"))
        assert out == []

    def test_search_tickers_sync_skips_exact_pass_for_freetext_query(self):
        """Lowercase / non-alnum queries shouldn't waste a ticker= round trip."""
        p = PolygonProvider(api_key="test")
        fake_client = MagicMock()
        fake_client.list_tickers.return_value = iter([
            _ref_ticker("AAPL", "Apple Inc."),
        ])
        with patch.object(p, "_rest_client", return_value=fake_client):
            p._search_tickers_sync("apple inc", limit=5)
        # Exactly one call: the fuzzy `search=` pass. No exact `ticker=` call.
        assert fake_client.list_tickers.call_count == 1
        kwargs = fake_client.list_tickers.call_args.kwargs
        assert "search" in kwargs and kwargs["search"] == "apple inc"
        assert "ticker" not in kwargs

    def test_search_tickers_sync_uses_both_passes_for_symbol_query(self):
        """Short alnum queries should fan out to both exact + fuzzy passes."""
        p = PolygonProvider(api_key="test")
        call_count = {"n": 0}

        def _list_tickers(**kwargs):
            call_count["n"] += 1
            return iter([_ref_ticker("AAPL", "Apple Inc.")])

        fake_client = MagicMock()
        fake_client.list_tickers.side_effect = _list_tickers
        with patch.object(p, "_rest_client", return_value=fake_client):
            p._search_tickers_sync("AAPL", limit=5)
        assert call_count["n"] == 2
        # First call passes `ticker=AAPL`; second passes `search=AAPL`.
        first_kwargs = fake_client.list_tickers.call_args_list[0].kwargs
        second_kwargs = fake_client.list_tickers.call_args_list[1].kwargs
        assert first_kwargs.get("ticker") == "AAPL"
        assert second_kwargs.get("search") == "AAPL"


# ---------- shared helpers ----------

def _wait_until(predicate, timeout: float = 1.0, interval: float = 0.02) -> bool:
    """
    Poll `predicate()` until it returns truthy or `timeout` seconds elapse.
    Returns the final value. Used to give cross-thread dispatch a chance to
    run before assertions.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
