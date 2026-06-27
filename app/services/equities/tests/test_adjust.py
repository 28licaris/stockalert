"""Unit tests for read-time split adjustment (app.services.equities.adjust).

Pure / no AWS. Covers the invariants the equivalence gate proves at scale:
cumulative future-split factor, searchsorted boundary (bar ON ex_date is
post-split), reverse splits, multi-split products, multi-symbol, and the
price ÷ / volume × / vwap+trade_count passthrough contract.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import pytest

from app.services.equities.adjust import (
    apply_adjustment,
    build_cum_factor_lookup,
)


def _raw(rows: list[dict]) -> pa.Table:
    """rows: dicts with symbol, ts(datetime), o,h,l,c,v, plus optional vwap/tc."""
    return pa.table({
        "symbol": pa.array([r["symbol"] for r in rows], pa.string()),
        "timestamp": pa.array([r["ts"] for r in rows], pa.timestamp("us", tz="UTC")),
        "open": pa.array([r["o"] for r in rows], pa.float64()),
        "high": pa.array([r["h"] for r in rows], pa.float64()),
        "low": pa.array([r["l"] for r in rows], pa.float64()),
        "close": pa.array([r["c"] for r in rows], pa.float64()),
        "volume": pa.array([r["v"] for r in rows], pa.float64()),
        "vwap": pa.array([r.get("vwap") for r in rows], pa.float64()),
        "trade_count": pa.array([r.get("tc") for r in rows], pa.int64()),
    })


def _ts(y, m, d, hh=14, mm=30):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _col(t: pa.Table, name: str) -> list:
    return t.column(name).to_pylist()


def test_no_splits_is_identity_with_factor_one():
    raw = _raw([
        {"symbol": "MSFT", "ts": _ts(2024, 1, 2), "o": 100.0, "h": 101.0,
         "l": 99.0, "c": 100.5, "v": 1000.0, "vwap": 100.2, "tc": 42},
    ])
    out = apply_adjustment(raw, build_cum_factor_lookup([]))
    assert _col(out, "adj_factor") == [1.0]
    assert _col(out, "open") == [100.0]
    assert _col(out, "close") == [100.5]
    assert _col(out, "volume") == [1000.0]
    # vwap + trade_count pass through unchanged
    assert _col(out, "vwap") == [100.2]
    assert _col(out, "trade_count") == [42]
    assert _col(out, "source") == ["polygon-adjusted"]


def test_single_split_pre_split_bar_adjusted():
    # 10:1 split on 2024-06-10. Pre-split bar halved-by-ten, on/after = raw.
    splits = [("NVDA", "2024-06-10", 10.0)]
    lookup = build_cum_factor_lookup(splits)
    raw = _raw([
        {"symbol": "NVDA", "ts": _ts(2024, 6, 9), "o": 1200.0, "h": 1200.0,
         "l": 1200.0, "c": 1200.0, "v": 100.0},   # pre-split
        {"symbol": "NVDA", "ts": _ts(2024, 6, 10), "o": 120.0, "h": 120.0,
         "l": 120.0, "c": 120.0, "v": 1000.0},     # ON ex_date = post-split
        {"symbol": "NVDA", "ts": _ts(2024, 6, 11), "o": 121.0, "h": 121.0,
         "l": 121.0, "c": 121.0, "v": 1000.0},     # post-split
    ])
    out = apply_adjustment(raw, lookup)
    f = _col(out, "adj_factor")
    assert f[0] == pytest.approx(10.0)
    assert f[1] == pytest.approx(1.0)
    assert f[2] == pytest.approx(1.0)
    assert _col(out, "close")[0] == pytest.approx(120.0)   # 1200 / 10
    assert _col(out, "volume")[0] == pytest.approx(1000.0)  # 100 * 10
    # post-split bars untouched
    assert _col(out, "close")[1] == pytest.approx(120.0)
    assert _col(out, "volume")[1] == pytest.approx(1000.0)


def test_multiple_splits_cumulative_product():
    # AAPL-like: 7:1 (2014-06-09) then 4:1 (2020-08-31).
    splits = [("AAPL", "2014-06-09", 7.0), ("AAPL", "2020-08-31", 4.0)]
    lookup = build_cum_factor_lookup(splits)
    raw = _raw([
        {"symbol": "AAPL", "ts": _ts(2013, 1, 2), "o": 560.0, "h": 560.0,
         "l": 560.0, "c": 560.0, "v": 1.0},   # before both → 28
        {"symbol": "AAPL", "ts": _ts(2016, 1, 4), "o": 100.0, "h": 100.0,
         "l": 100.0, "c": 100.0, "v": 1.0},   # between → 4
        {"symbol": "AAPL", "ts": _ts(2021, 1, 4), "o": 130.0, "h": 130.0,
         "l": 130.0, "c": 130.0, "v": 1.0},   # after both → 1
    ])
    f = _col(apply_adjustment(raw, lookup), "adj_factor")
    assert f[0] == pytest.approx(28.0)
    assert f[1] == pytest.approx(4.0)
    assert f[2] == pytest.approx(1.0)


def test_reverse_split_factor_below_one():
    # 1:5 reverse split → factor 0.2. Pre-split prices scaled UP (÷0.2 = ×5).
    splits = [("ABC", "2024-03-01", 0.2)]
    lookup = build_cum_factor_lookup(splits)
    raw = _raw([
        {"symbol": "ABC", "ts": _ts(2024, 2, 1), "o": 1.0, "h": 1.0,
         "l": 1.0, "c": 1.0, "v": 500.0},
    ])
    out = apply_adjustment(raw, lookup)
    assert _col(out, "adj_factor")[0] == pytest.approx(0.2)
    assert _col(out, "close")[0] == pytest.approx(5.0)     # 1.0 / 0.2
    assert _col(out, "volume")[0] == pytest.approx(100.0)  # 500 * 0.2


def test_factor_one_split_is_ignored():
    # A recorded split with factor 1.0 is a no-op and must not appear.
    lookup = build_cum_factor_lookup([("X", "2024-01-01", 1.0)])
    assert "X" not in lookup


def test_multi_symbol_independent_factors():
    splits = [("NVDA", "2024-06-10", 10.0)]  # only NVDA splits
    lookup = build_cum_factor_lookup(splits)
    raw = _raw([
        {"symbol": "NVDA", "ts": _ts(2024, 6, 9), "o": 1200.0, "h": 1200.0,
         "l": 1200.0, "c": 1200.0, "v": 1.0},
        {"symbol": "MSFT", "ts": _ts(2024, 6, 9), "o": 400.0, "h": 400.0,
         "l": 400.0, "c": 400.0, "v": 1.0},
    ])
    out = apply_adjustment(raw, lookup)
    # row order preserved; NVDA adjusted, MSFT untouched
    assert _col(out, "symbol") == ["NVDA", "MSFT"]
    assert _col(out, "adj_factor") == [pytest.approx(10.0), pytest.approx(1.0)]
    assert _col(out, "close") == [pytest.approx(120.0), pytest.approx(400.0)]


def test_empty_table():
    out = apply_adjustment(_raw([]), build_cum_factor_lookup([]))
    assert out.num_rows == 0
    assert set(out.column_names) >= {"symbol", "timestamp", "close", "adj_factor"}


def test_multiple_splits_same_ex_date_collapse():
    # Two split rows on the same ex_date multiply (sum of logs).
    splits = [("Y", "2024-01-10", 2.0), ("Y", "2024-01-10", 3.0)]
    lookup = build_cum_factor_lookup(splits)
    raw = _raw([
        {"symbol": "Y", "ts": _ts(2024, 1, 9), "o": 6.0, "h": 6.0,
         "l": 6.0, "c": 6.0, "v": 1.0},
    ])
    assert _col(apply_adjustment(raw, lookup), "adj_factor")[0] == pytest.approx(6.0)
