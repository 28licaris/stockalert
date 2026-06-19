"""EW-1: causal pivot detector."""
from __future__ import annotations

import pandas as pd

from app.indicators.pivots import PivotDetector, detect_multidegree
from app.indicators.registry import get_indicator
from tests._ewt_synthetic import synthetic_ohlc


def test_pivot_high_and_low_on_synthetic():
    close, high, low = synthetic_ohlc()
    piv = PivotDetector(period=3, source="hl").detect(close, high, low)
    kinds = {p.kind for p in piv}
    assert kinds == {"high", "low"}
    # the wave-1 top (~120) should appear as a high pivot
    assert any(p.kind == "high" and 118 <= p.price <= 122 for p in piv)


def test_confirmed_at_index_is_i_plus_k():
    close, high, low = synthetic_ohlc()
    for p in PivotDetector(period=5, source="hl").detect(close, high, low):
        assert p.confirmed_at_index == p.index + p.k


def test_no_pivots_in_warmup_edges():
    close, high, low = synthetic_ohlc()
    k = 4
    piv = PivotDetector(period=k, source="hl").detect(close, high, low)
    n = len(close)
    assert all(k <= p.index < n - k for p in piv)


def test_hl_vs_close_source_differ():
    close, high, low = synthetic_ohlc()
    hl = PivotDetector(period=3, source="hl").detect(close, high, low)
    cl = PivotDetector(period=3, source="close").detect(close)
    # hl highs sit at the wick (close + 0.2), strictly above the close-based high
    hl_high = max(p.price for p in hl if p.kind == "high")
    cl_high = max(p.price for p in cl if p.kind == "high")
    assert hl_high > cl_high


def test_degree_ordering_fewer_with_larger_k():
    close, high, low = synthetic_ohlc()
    small = PivotDetector(period=3, source="hl").detect(close, high, low)
    big = PivotDetector(period=8, source="hl").detect(close, high, low)
    assert len(big) <= len(small)


def test_multidegree_tags_degree():
    close, high, low = synthetic_ohlc()
    piv = detect_multidegree(close, high, low, ks=(3, 5))
    degrees = {p.degree for p in piv}
    assert degrees <= {0, 1}
    assert piv == sorted(piv, key=lambda p: (p.index, p.degree))


def test_compute_returns_signed_series():
    close, high, low = synthetic_ohlc()
    sig = PivotDetector(period=3, source="hl").compute(close, high, low)
    assert set(sig.unique()) <= {-1, 0, 1}
    assert (sig != 0).sum() > 0


def test_registry_roundtrip():
    det = get_indicator("pivots", period=8)
    assert isinstance(det, PivotDetector)
    assert det.period == 8


def test_determinism():
    close, high, low = synthetic_ohlc()
    a = PivotDetector(period=3, source="hl").detect(close, high, low)
    b = PivotDetector(period=3, source="hl").detect(close, high, low)
    assert [p.model_dump() for p in a] == [p.model_dump() for p in b]
