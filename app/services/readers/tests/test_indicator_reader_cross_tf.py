"""
Cross-timeframe (source-aggregation) indicator engine tests.

Unit-level — the ClickHouse fetch (`IndicatorReader._fetch_bars`) is
monkeypatched with synthetic bars, so these run without a live CH and
pin the engine's logic, not the data plane:

  - request resolution (bar-locked / window-locked / same-TF / rejects),
  - forward-fill "step" semantics onto the display axis,
  - a 1d SMA drawn on a 5m chart equals the daily SMA stepped across each
    day, with warmup bars rendered as None.

These are the numbers the chart AND the MA-crossover alert engine read,
so they are the cross-surface correctness contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.services.readers.indicator_reader import (
    IndicatorReader,
    _forward_fill_onto,
    _warmup_lookback,
)
from app.services.readers.schemas import BronzeBar

UTC = timezone.utc


def _bar(ts: datetime, close: float, *, interval_symbol: str = "TEST") -> BronzeBar:
    return BronzeBar(
        symbol=interval_symbol,
        timestamp=ts,
        open=close, high=close + 0.5, low=close - 0.5, close=close,
        volume=1_000.0, vwap=close, trade_count=10, source="test",
    )


def _daily_bars(n: int, *, start_close: float = 100.0, step: float = 1.0) -> list[BronzeBar]:
    """`n` daily bars timestamped at 00:00 UTC, day 0..n-1, ramping closes."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        _bar(base + timedelta(days=i), start_close + i * step) for i in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────
# Request resolution
# ─────────────────────────────────────────────────────────────────────


def test_resolve_bar_locked() -> None:
    r = IndicatorReader()
    source_agg, params = r._resolve_source_agg(
        {"name": "sma", "params": {"period": 200}, "source_agg": "1d"}, "5m"
    )
    assert source_agg == "1d"
    assert params == {"period": 200}


def test_resolve_window_locked_pins_daily() -> None:
    r = IndicatorReader()
    source_agg, params = r._resolve_source_agg(
        {"name": "sma", "params": {}, "window_days": 200}, "5m"
    )
    assert source_agg == "1d"
    assert params["period"] == 200


def test_resolve_same_tf_returns_none() -> None:
    """source_agg equal to display interval is the ordinary single-TF path."""
    r = IndicatorReader()
    source_agg, _ = r._resolve_source_agg(
        {"name": "sma", "params": {"period": 20}, "source_agg": "5m"}, "5m"
    )
    assert source_agg is None


def test_resolve_no_cross_tf_returns_none() -> None:
    r = IndicatorReader()
    source_agg, params = r._resolve_source_agg(
        {"name": "sma", "params": {"period": 20}}, "1d"
    )
    assert source_agg is None
    assert params == {"period": 20}


def test_resolve_rejects_finer_source() -> None:
    r = IndicatorReader()
    with pytest.raises(ValueError, match="finer than display"):
        r._resolve_source_agg(
            {"name": "sma", "params": {"period": 20}, "source_agg": "5m"}, "1h"
        )


def test_resolve_rejects_unknown_source() -> None:
    r = IndicatorReader()
    with pytest.raises(ValueError, match="not supported"):
        r._resolve_source_agg(
            {"name": "sma", "params": {"period": 20}, "source_agg": "3y"}, "5m"
        )


# ─────────────────────────────────────────────────────────────────────
# Forward-fill ("step") semantics
# ─────────────────────────────────────────────────────────────────────


def test_forward_fill_steps_and_masks_pre_window() -> None:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    # Source MA: NaN, NaN, then values at day 2 and day 3.
    src_idx = pd.DatetimeIndex([base + timedelta(days=i) for i in range(4)])
    src = pd.Series([np.nan, np.nan, 10.0, 20.0], index=src_idx)

    # Display bars: before any value, on day 2, on day 3 (intraday).
    disp_idx = pd.DatetimeIndex([
        base + timedelta(days=1, hours=10),   # only day0/day1 known (both NaN) -> None
        base + timedelta(days=2, hours=10),   # steps to day2 value 10.0
        base + timedelta(days=2, hours=15),   # still 10.0
        base + timedelta(days=3, hours=10),   # steps to day3 value 20.0
    ])
    out = _forward_fill_onto(src, disp_idx)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == pytest.approx(10.0)
    assert out.iloc[2] == pytest.approx(10.0)
    assert out.iloc[3] == pytest.approx(20.0)


def test_forward_fill_preserves_display_order() -> None:
    """merge_asof needs sorted keys; result must still align 1:1 to input order."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    src = pd.Series(
        [1.0, 2.0, 3.0],
        index=pd.DatetimeIndex([base + timedelta(days=i) for i in range(3)]),
    )
    # Deliberately unsorted display index.
    disp_idx = pd.DatetimeIndex([
        base + timedelta(days=2, hours=1),
        base + timedelta(days=0, hours=1),
        base + timedelta(days=1, hours=1),
    ])
    out = _forward_fill_onto(src, disp_idx)
    assert out.iloc[0] == pytest.approx(3.0)
    assert out.iloc[1] == pytest.approx(1.0)
    assert out.iloc[2] == pytest.approx(2.0)


def test_warmup_lookback_scales_with_bars() -> None:
    small = _warmup_lookback("1d", 50)
    big = _warmup_lookback("1d", 200)
    assert big > small
    # Daily lookback comfortably exceeds the raw bar count (closed days).
    assert _warmup_lookback("1d", 200).days > 200


# ─────────────────────────────────────────────────────────────────────
# End-to-end: 1d SMA stepped onto a 5m chart
# ─────────────────────────────────────────────────────────────────────


def test_daily_sma_on_intraday_matches_daily_series(monkeypatch) -> None:
    """
    A 5m chart on the last day with a 5-day SMA(source_agg=1d) must show the
    daily SMA value computed through that day, flat across every 5m bar —
    and equal to a direct SMA over the trailing 5 daily closes.
    """
    r = IndicatorReader()
    daily = _daily_bars(40)  # closes 100..139
    last_day = daily[-1].timestamp  # 2024-02-09 00:00 UTC

    # 5m display bars across the last day's morning.
    disp_idx = pd.DatetimeIndex([
        last_day + timedelta(hours=14, minutes=30 + 5 * k) for k in range(6)
    ])

    def fake_fetch(symbol, start, end, interval):
        assert interval == "1d"  # cross-TF should request the source agg
        return daily, None

    monkeypatch.setattr(r, "_fetch_bars", fake_fetch)

    out = r._compute_cross_tf(
        "TEST", "sma", {"period": 5}, "1d",
        display_index=disp_idx, end=last_day + timedelta(days=1),
    )

    # Direct daily SMA(5) through the last day: mean of closes 135..139.
    expected = float(np.mean([135.0, 136.0, 137.0, 138.0, 139.0]))
    assert (out == out).all()  # no NaN — all 5m bars are after the warmup
    for v in out.to_numpy():
        assert v == pytest.approx(expected)


def test_chart_data_echoes_source_agg_and_label(monkeypatch) -> None:
    """The full get_chart_data path tags the series with source_agg + label."""
    r = IndicatorReader()
    daily = _daily_bars(40)
    base = daily[-1].timestamp

    disp_idx = [base + timedelta(hours=14, minutes=30 + 5 * k) for k in range(3)]
    display_bars = [_bar(ts, 200.0 + i) for i, ts in enumerate(disp_idx)]

    def fake_fetch(symbol, start, end, interval):
        return (display_bars if interval == "5m" else daily), None

    monkeypatch.setattr(r, "_fetch_bars", fake_fetch)

    chart = r.get_chart_data(
        "TEST",
        [{"name": "sma", "params": {"period": 5}, "source_agg": "1d"}],
        start=disp_idx[0], end=disp_idx[-1] + timedelta(minutes=5),
        interval="5m",
    )
    assert len(chart.series) == 1
    s = chart.series[0]
    assert s.source_agg == "1d"
    assert "1d" in s.label
    assert s.count == len(display_bars)
    # Every value is the same daily SMA stepped flat.
    vals = [v.value for v in s.values if v.value is not None]
    assert len(vals) == len(display_bars)
    assert all(v == pytest.approx(vals[0]) for v in vals)


def test_cross_tf_rejects_multi_output(monkeypatch) -> None:
    r = IndicatorReader()
    monkeypatch.setattr(r, "_fetch_bars", lambda *a, **k: (_daily_bars(40), None))
    disp_idx = pd.DatetimeIndex([datetime(2024, 2, 9, 14, tzinfo=UTC)])
    with pytest.raises(ValueError, match="multi-output"):
        r._compute_cross_tf(
            "TEST", "bollinger", {"period": 20}, "1d",
            display_index=disp_idx, end=datetime(2024, 2, 10, tzinfo=UTC),
        )
