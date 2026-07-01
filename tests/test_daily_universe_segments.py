"""Segment-trim correctness for the ohlcv_daily research universe.

Polygon keys rows by TICKER; a reused ticker holds multiple companies'
histories separated by multi-month gaps (V = Vivendi'06 → Visa'08+). The
dominant-segment picker must keep exactly the economically dominant
contiguous run — these tests pin that contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_ohlcv_daily import dominant_segment_bounds, segment_trim  # noqa: E402


def _df(sym: str, dates: list[str], close: float = 100.0, volume: float = 1e6) -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": sym,
        "timestamp": pd.to_datetime(dates).tz_localize("UTC") + pd.Timedelta(hours=14, minutes=30),
        "open": close, "high": close, "low": close, "close": close, "volume": volume,
    })


def _bdays(start: str, periods: int) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=periods)]


class TestDominantSegmentBounds:
    def test_single_contiguous_history_kept_whole(self):
        cal = np.arange(500)
        s, e = dominant_segment_bounds(cal, np.full(500, 1e6))
        assert (s, e) == (0, 499)

    def test_ticker_reuse_keeps_high_dollar_segment(self):
        # old company: 100 bars tiny volume · 120-day gap · new company: 300 bars big
        cal = np.concatenate([np.arange(100), np.arange(220, 520)])
        dollars = np.concatenate([np.full(100, 1e4), np.full(300, 1e8)])
        s, e = dominant_segment_bounds(cal, dollars)
        assert (s, e) == (100, 399)

    def test_dominant_by_dollar_not_length(self):
        # Bear Stearns case: FIRST segment shorter-lived successor but massive volume
        cal = np.concatenate([np.arange(200), np.arange(300, 800)])
        dollars = np.concatenate([np.full(200, 1e9), np.full(500, 1e3)])
        s, e = dominant_segment_bounds(cal, dollars)
        assert (s, e) == (0, 199)

    def test_short_halt_does_not_split(self):
        # 5-trading-day halt (== max_gap) keeps one segment
        cal = np.concatenate([np.arange(100), np.arange(105, 200)])
        s, e = dominant_segment_bounds(cal, np.full(195, 1e6))
        assert (s, e) == (0, 194)

    def test_gap_just_over_threshold_splits(self):
        cal = np.concatenate([np.arange(100), np.arange(106, 200)])
        dollars = np.concatenate([np.full(100, 1e6), np.full(94, 2e6)])
        s, e = dominant_segment_bounds(cal, dollars)
        assert (s, e) == (100, 193)

    def test_empty_history_raises(self):
        with pytest.raises(ValueError):
            dominant_segment_bounds(np.array([], dtype=int), np.array([]))


class TestSegmentTrim:
    def test_reused_ticker_trimmed_clean_symbol_untouched(self):
        # AAPL continuous 400 days; V = 60 Vivendi days + gap + 300 Visa days.
        cal = _bdays("2006-01-03", 400)
        aapl = _df("AAPL", cal)
        v = pd.concat([_df("V", cal[:60], close=30, volume=1e5),
                       _df("V", cal[120:], close=80, volume=5e7)])
        out = segment_trim(pd.concat([aapl, v], ignore_index=True))
        assert len(out[out.symbol == "AAPL"]) == 400
        kept_v = out[out.symbol == "V"]
        assert len(kept_v) == 280
        assert kept_v.timestamp.min() == pd.Timestamp(cal[120], tz="UTC") + pd.Timedelta(hours=14, minutes=30)

    def test_junk_tail_dropped(self):
        # FB case: dense 2012-22 history + sparse post-rename junk rows
        cal = _bdays("2012-05-18", 600)
        dense = _df("FB", cal[:500], volume=5e7)
        junk = _df("FB", cal[520::10], volume=1e3)  # every 10th day → all gaps > max_gap
        out = segment_trim(pd.concat([dense, junk], ignore_index=True))
        assert len(out) == 500

    def test_no_trim_needed_is_identity(self):
        cal = _bdays("2020-01-02", 250)
        df = pd.concat([_df("SPY", cal), _df("QQQ", cal)], ignore_index=True)
        out = segment_trim(df)
        assert len(out) == len(df)
        assert set(out.symbol) == {"SPY", "QQQ"}
