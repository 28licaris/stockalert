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


def test_basket_seam_raises_not_implemented():
    g = RotationGroup(
        id="ai-datacenters",
        name="AI Datacenters",
        kind="basket",
        benchmark="SPY",
        members=["NVDA", "AMD", "AVGO"],
    )
    with pytest.raises(NotImplementedError):
        resolve(g)
