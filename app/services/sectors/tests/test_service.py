"""Service tests — dashboard assembly + exclusion surfacing."""
from __future__ import annotations

import numpy as np
import pytest
import pandas as pd

from app.services.sectors import service as service_mod
from app.services.sectors.resolver import GroupResolutionError
from app.services.sectors.schemas import RotationGroup
from app.services.sectors.service import SectorRotationService


def _bdates(n: int) -> pd.Index:
    return pd.Index([d.date() for d in pd.bdate_range("2024-01-01", periods=n)])


def _flat(n: int, val: float = 100.0) -> pd.Series:
    return pd.Series([val] * n, index=_bdates(n), dtype="float64")


def _rising(n: int, slope: float) -> pd.Series:
    return pd.Series(100.0 + np.arange(n) * slope, index=_bdates(n), dtype="float64")


def _accel(n: int, k: float) -> pd.Series:
    """Super-exponential series 100·exp(k·t²) — still-accelerating up (k>0)
    or down (k<0), so rs_ratio and rs_momentum stay on the same side of 100."""
    return pd.Series(100.0 * np.exp(k * np.arange(n) ** 2), index=_bdates(n), dtype="float64")


def _svc(groups, closes=None, loader=None) -> SectorRotationService:
    return SectorRotationService(
        groups=groups,
        benchmark="SPY",
        ratio_window=5,
        mom_window=3,
        tail_weeks=4,
        lookback_days=400,
        closes_loader=loader or (lambda symbols, days: closes or {}),
    )


def test_build_dashboard_scores_and_excludes(monkeypatch):
    n = 80
    bench = _flat(n)
    up = _accel(n, 0.0006)     # accelerating outperformance → leading
    down = _accel(n, -0.0006)  # accelerating underperformance → lagging

    groups = [
        RotationGroup(id="UP", name="Up", benchmark="SPY", members=["UP"]),
        RotationGroup(id="DOWN", name="Down", benchmark="SPY", members=["DOWN"]),
        RotationGroup(id="DEAD", name="Dead", benchmark="SPY", members=["DEAD"]),
    ]

    def fake_resolve(group, closes=None):
        if group.id == "SPY":
            return bench
        if group.id == "UP":
            return up
        if group.id == "DOWN":
            return down
        raise GroupResolutionError("no lake bars")

    monkeypatch.setattr(service_mod, "resolve", fake_resolve)

    dash = _svc(groups).build_dashboard()

    assert dash.benchmark == "SPY"
    scored = {s.group_id: s for s in dash.sectors}
    assert scored["UP"].current.quadrant == "leading"
    assert scored["DOWN"].current.quadrant == "lagging"
    # DEAD couldn't resolve → surfaced in excluded, not silently dropped.
    assert [e.group_id for e in dash.excluded] == ["DEAD"]
    assert "no lake bars" in dash.excluded[0].reason


def test_thin_data_group_is_excluded(monkeypatch):
    bench = _flat(80)
    thin = _rising(4, 1.0)  # too short to warm the SMAs

    groups = [RotationGroup(id="THIN", name="Thin", benchmark="SPY", members=["THIN"])]

    def fake_resolve(group, closes=None):
        return bench if group.id == "SPY" else thin

    monkeypatch.setattr(service_mod, "resolve", fake_resolve)

    dash = _svc(groups).build_dashboard()
    assert dash.sectors == []
    assert dash.excluded[0].group_id == "THIN"


# ── the daily-source contract: one query, named reasons, loading state ──


def test_dashboard_loads_every_symbol_in_one_call():
    n = 80
    closes = {"SPY": _flat(n), "UP": _accel(n, 0.0006), "B1": _accel(n, 0.0005), "B2": _accel(n, 0.0007)}
    calls = []

    def loader(symbols, days):
        calls.append((list(symbols), days))
        return closes

    groups = [
        RotationGroup(id="UP", name="Up", benchmark="SPY", members=["UP"]),
        RotationGroup(id="BASK", name="Basket", kind="basket", benchmark="SPY", members=["B1", "B2"]),
    ]
    dash = _svc(groups, loader=loader).build_dashboard()

    assert len(calls) == 1, "one batched load for the benchmark and all members"
    assert calls[0][0] == ["B1", "B2", "SPY", "UP"] and calls[0][1] == 400
    assert {s.group_id for s in dash.sectors} == {"UP", "BASK"}


def test_missing_daily_data_is_excluded_with_the_symbol_named():
    n = 80
    groups = [RotationGroup(id="XLB", name="Materials", benchmark="SPY", members=["XLB"])]
    dash = _svc(groups, closes={"SPY": _flat(n)}).build_dashboard()
    assert [(e.group_id, e.reason) for e in dash.excluded] == [("XLB", "no daily data for XLB")]


def test_too_little_history_is_excluded_with_the_session_counts():
    groups = [RotationGroup(id="NEW", name="New", benchmark="SPY", members=["NEW"])]
    closes = {"SPY": _flat(80), "NEW": _accel(5, 0.001)}
    dash = _svc(groups, closes=closes).build_dashboard()
    assert dash.excluded[0].group_id == "NEW" and "insufficient history" in dash.excluded[0].reason


def test_missing_benchmark_fails_loudly():
    groups = [RotationGroup(id="UP", name="Up", benchmark="SPY", members=["UP"])]
    with pytest.raises(GroupResolutionError, match="benchmark 'SPY' has no data"):
        _svc(groups, closes={"UP": _accel(80, 0.0006)}).build_dashboard()


def test_personal_theme_being_fetched_reads_as_loading_not_failure(monkeypatch):
    groups = [RotationGroup(id="my:ura", name="Uranium", kind="basket", benchmark="SPY",
                            members=["CCJ", "UEC"], personal=True)]
    monkeypatch.setattr(service_mod, "is_fetching", lambda sym: sym == "CCJ")
    dash = _svc(groups, closes={"SPY": _flat(80)}).build_dashboard()
    assert dash.excluded[0].reason == "loading price history for CCJ"


def test_shared_group_is_never_reported_as_loading(monkeypatch):
    groups = [RotationGroup(id="ura", name="Uranium", kind="basket", benchmark="SPY", members=["CCJ"])]
    monkeypatch.setattr(service_mod, "is_fetching", lambda sym: True)
    dash = _svc(groups, closes={"SPY": _flat(80)}).build_dashboard()
    assert "loading" not in dash.excluded[0].reason
