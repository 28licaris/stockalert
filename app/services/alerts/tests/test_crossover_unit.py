"""MA-crossover detector + scan tests (no AWS / no live CH)."""
from __future__ import annotations

import datetime as dt
from datetime import timezone

import pytest

from app.services.alerts.crossover import detect_crossings, scan_ma_crossovers
from app.services.alerts.schemas import MACrossoverAlert
from app.services.readers.schemas import (
    BronzeBar,
    IndicatorChartData,
    IndicatorSeries,
    IndicatorValue,
)

UTC = timezone.utc
T0 = dt.datetime(2024, 6, 3, 14, 30, tzinfo=UTC)


def _ts(i: int) -> dt.datetime:
    return T0 + dt.timedelta(minutes=5 * i)


# ─────────────────────────────────────────────────────────────────────
# detect_crossings — pure
# ─────────────────────────────────────────────────────────────────────


def test_detects_bullish_and_bearish() -> None:
    price = [9.0, 11.0, 12.0, 8.0]   # up-cross at 1, down-cross at 3
    ma = [10.0, 10.0, 10.0, 10.0]
    ts = [_ts(i) for i in range(4)]
    crossings = detect_crossings(price, ma, ts)
    assert crossings == [(1, "bullish"), (3, "bearish")]


def test_no_fire_during_warmup_none() -> None:
    """None MA (warmup) on either side of a transition must not fire."""
    price = [9.0, 11.0, 12.0]
    ma = [None, None, 10.0]  # first valid MA at index 2; no prior to compare
    crossings = detect_crossings(price, ma, [_ts(i) for i in range(3)])
    assert crossings == []


def test_touch_then_below_fires_bearish_tradingview() -> None:
    """
    Price touches the MA (diff == 0) then moves strictly below. TradingView
    `ta.crossunder` treats the touch as "at or above", so the drop fires a
    single bearish event at the bar that goes below — not on the touch bar.
    """
    price = [9.0, 10.0, 9.5]
    ma = [10.0, 10.0, 10.0]
    crossings = detect_crossings(price, ma, [_ts(i) for i in range(3)])
    assert crossings == [(2, "bearish")]


def test_cross_up_from_exact_touch() -> None:
    """diff goes 0 -> positive counts as a bullish cross (prev_diff <= 0)."""
    price = [10.0, 11.0]
    ma = [10.0, 10.0]
    assert detect_crossings(price, ma, [_ts(0), _ts(1)]) == [(1, "bullish")]


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        detect_crossings([1.0, 2.0], [1.0], [_ts(0), _ts(1)])


# ─────────────────────────────────────────────────────────────────────
# scan_ma_crossovers — wired to a fake reader
# ─────────────────────────────────────────────────────────────────────


class _FakeReader:
    """Returns a canned IndicatorChartData; records the spec it was given."""

    def __init__(self, bars, ma_values, *, source_agg, period):
        self._bars = bars
        self._ma_values = ma_values
        self._source_agg = source_agg
        self._period = period
        self.last_spec = None

    def get_chart_data(self, symbol, specs, *, start, end, interval):
        self.last_spec = specs[0]
        series = IndicatorSeries(
            name="sma",
            params={"period": self._period},
            label="SMA",
            values=self._ma_values,
            count=len(self._ma_values),
            source_agg=self._source_agg,
        )
        return IndicatorChartData(
            symbol=symbol, interval=interval, start=start, end=end,
            bars=self._bars, series=[series], snapshot_id=None,
        )


def _bar(i: int, close: float) -> BronzeBar:
    return BronzeBar(
        symbol="AAPL", timestamp=_ts(i),
        open=close, high=close + 0.5, low=close - 0.5, close=close,
        volume=1000.0, vwap=close, trade_count=5, source="test",
    )


def test_scan_emits_payload_with_source_agg() -> None:
    bars = [_bar(0, 9.0), _bar(1, 11.0), _bar(2, 8.0)]
    ma_vals = [IndicatorValue(timestamp=_ts(i), value=10.0) for i in range(3)]
    reader = _FakeReader(bars, ma_vals, source_agg="1d", period=200)

    alerts = scan_ma_crossovers(
        "AAPL", ma="sma", start=_ts(0), end=_ts(3),
        display_agg="5m", source_agg="1d", length=200, reader=reader,
    )
    assert [a.direction for a in alerts] == ["bullish", "bearish"]
    a = alerts[0]
    assert isinstance(a, MACrossoverAlert)
    assert a.source_agg == "1d"
    assert a.display_agg == "5m"
    assert a.length == 200
    assert a.ma == "sma"
    assert a.price == 11.0 and a.ma_value == 10.0
    assert a.setup == "sma200_1d_cross_above"
    # The cross-TF spec was forwarded to the reader.
    assert reader.last_spec["source_agg"] == "1d"


def test_scan_same_tf_source_defaults_to_display() -> None:
    """No source_agg → series.source_agg None → payload source_agg == display."""
    bars = [_bar(0, 9.0), _bar(1, 11.0)]
    ma_vals = [IndicatorValue(timestamp=_ts(i), value=10.0) for i in range(2)]
    reader = _FakeReader(bars, ma_vals, source_agg=None, period=20)

    alerts = scan_ma_crossovers(
        "AAPL", ma="sma", start=_ts(0), end=_ts(2),
        display_agg="1d", length=20, reader=reader,
    )
    assert len(alerts) == 1
    assert alerts[0].source_agg == "1d"
    assert alerts[0].length == 20


def test_scan_rejects_unknown_ma() -> None:
    with pytest.raises(ValueError, match="ma must be one of"):
        scan_ma_crossovers("AAPL", ma="bollinger", start=_ts(0), end=_ts(1))
