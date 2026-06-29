"""Service tests — dashboard assembly + exclusion surfacing."""
from __future__ import annotations

import numpy as np
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


def _svc(groups) -> SectorRotationService:
    return SectorRotationService(
        groups=groups,
        benchmark="SPY",
        ratio_window=5,
        mom_window=3,
        tail_weeks=4,
        lookback_days=400,
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

    def fake_resolve(group, *, lookback_days=None):
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

    def fake_resolve(group, *, lookback_days=None):
        return bench if group.id == "SPY" else thin

    monkeypatch.setattr(service_mod, "resolve", fake_resolve)

    dash = _svc(groups).build_dashboard()
    assert dash.sectors == []
    assert dash.excluded[0].group_id == "THIN"
