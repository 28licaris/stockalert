"""Resolver tests — ETF passthrough + the Phase-2 basket seam."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.sectors import resolver
from app.services.sectors.resolver import GroupResolutionError, resolve
from app.services.sectors.schemas import RotationGroup


def _bar(day: int, close: float):
    return SimpleNamespace(
        timestamp=datetime(2024, 1, day, tzinfo=timezone.utc), close=close
    )


def test_etf_passthrough_returns_close_series(monkeypatch):
    bars = [_bar(2, 10.0), _bar(3, 11.0), _bar(4, 12.0)]
    monkeypatch.setattr(resolver, "get_chart_bars", lambda *a, **k: bars)

    g = RotationGroup(id="XLK", name="Technology", benchmark="SPY", members=["XLK"])
    s = resolve(g, lookback_days=400)

    assert list(s.values) == [10.0, 11.0, 12.0]
    assert s.is_monotonic_increasing  # sorted by date


def test_etf_dedupes_duplicate_days(monkeypatch):
    # polygon∪schwab union can hand back the same day twice — keep last.
    bars = [_bar(2, 10.0), _bar(2, 10.5), _bar(3, 11.0)]
    monkeypatch.setattr(resolver, "get_chart_bars", lambda *a, **k: bars)

    g = RotationGroup(id="XLK", name="Technology", benchmark="SPY", members=["XLK"])
    s = resolve(g, lookback_days=400)

    assert len(s) == 2
    assert s.iloc[0] == 10.5  # kept the last of the duplicate day


def test_empty_lake_raises(monkeypatch):
    monkeypatch.setattr(resolver, "get_chart_bars", lambda *a, **k: [])
    g = RotationGroup(id="XLB", name="Materials", benchmark="SPY", members=["XLB"])
    with pytest.raises(GroupResolutionError):
        resolve(g)


def test_despike_removes_reverting_spike(monkeypatch):
    # 146 → 291 (2x) → 146: a bad after-hours tick that reverts. Despiked.
    bars = [_bar(2, 146.0), _bar(3, 291.0), _bar(4, 146.0), _bar(5, 147.0)]
    monkeypatch.setattr(resolver, "get_chart_bars", lambda *a, **k: bars)
    g = RotationGroup(id="XLK", name="Technology", benchmark="SPY", members=["XLK"])
    s = resolve(g)
    assert s.iloc[1] == 146.0  # spike replaced by neighbour mean (146+146)/2
    assert s.iloc[0] == 146.0 and s.iloc[2] == 146.0  # neighbours untouched


def test_despike_preserves_genuine_step(monkeypatch):
    # A real step that does NOT revert (100 → 160 → 162) must be kept.
    bars = [_bar(2, 100.0), _bar(3, 160.0), _bar(4, 162.0), _bar(5, 161.0)]
    monkeypatch.setattr(resolver, "get_chart_bars", lambda *a, **k: bars)
    g = RotationGroup(id="XLK", name="Technology", benchmark="SPY", members=["XLK"])
    s = resolve(g)
    assert list(s.values) == [100.0, 160.0, 162.0, 161.0]  # nothing despiked


def _series_bars(values, day0=2):
    return [_bar(day0 + i, v) for i, v in enumerate(values)]


def test_basket_equal_weight_composite(monkeypatch):
    # Two members: one flat at 100, one doubling 100→200 over 3 days.
    # Equal-weight rebased composite: day0=100, then avg(1.0, growth)*100.
    data = {
        "AAA": _series_bars([100.0, 100.0, 100.0]),
        "BBB": _series_bars([100.0, 150.0, 200.0]),
    }
    monkeypatch.setattr(resolver, "get_chart_bars", lambda symbol, **k: data[symbol])
    g = RotationGroup(id="MINERS", name="Miners", kind="basket", benchmark="SPY",
                      members=["AAA", "BBB"])
    s = resolve(g)
    # day0: (1.0 + 1.0)/2 * 100 = 100
    assert s.iloc[0] == pytest.approx(100.0)
    # day2: (1.0 + 2.0)/2 * 100 = 150
    assert s.iloc[-1] == pytest.approx(150.0)


def test_basket_drops_missing_members(monkeypatch):
    data = {"AAA": _series_bars([10.0, 11.0, 12.0]), "GHOST": []}
    monkeypatch.setattr(resolver, "get_chart_bars", lambda symbol, **k: data.get(symbol, []))
    g = RotationGroup(id="MINERS", name="Miners", kind="basket", benchmark="SPY",
                      members=["AAA", "GHOST"])
    s = resolve(g)  # GHOST dropped (no data); AAA carries the basket
    assert len(s) == 3
    assert s.iloc[0] == pytest.approx(100.0)  # rebased


def test_basket_all_missing_raises(monkeypatch):
    monkeypatch.setattr(resolver, "get_chart_bars", lambda symbol, **k: [])
    g = RotationGroup(id="MINERS", name="Miners", kind="basket", benchmark="SPY",
                      members=["X", "Y"])
    with pytest.raises(GroupResolutionError):
        resolve(g)


def test_basket_weighted(monkeypatch):
    # AAA flat, BBB doubles; weight BBB at 0.75 → day2 = 1.0*0.25 + 2.0*0.75 = 1.75
    data = {
        "AAA": _series_bars([100.0, 100.0, 100.0]),
        "BBB": _series_bars([100.0, 150.0, 200.0]),
    }
    monkeypatch.setattr(resolver, "get_chart_bars", lambda symbol, **k: data[symbol])
    g = RotationGroup(id="MINERS", name="Miners", kind="basket", benchmark="SPY",
                      members=["AAA", "BBB"], weights={"AAA": 0.25, "BBB": 0.75})
    s = resolve(g)
    assert s.iloc[-1] == pytest.approx(175.0)
