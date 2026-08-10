"""PIT harness — does it detect a miscalibrated fan?

The load-bearing tests here are the *negative* ones. A harness that reports
"calibrated" for everything is worse than no harness, so each test below feeds
it a distribution that is knowably wrong in a specific direction and asserts the
statistics point that way.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services.ppl.calibrate import (
    COVERAGE_TOLERANCE,
    CoverageBand,
    ks_uniform,
    run_pit,
    split_by_volatility_regime,
)
from app.services.ppl.schemas import PplParams
from app.services.ppl.service import PplService
from app.services.ppl.tests.conftest import bars_to_frame, make_bars


def _service(bars=None) -> PplService:
    return PplService(bars_fn=lambda s, **_: bars or [])


# ── coverage bands ────────────────────────────────────────────────────


def test_standard_error_scales_with_n():
    """±5pp is meaningless without a noise floor: at n=50 it is inside noise,
    at n=2000 it is a gross miss."""
    small = CoverageBand(quantile=0.15, realized=0.20, n=50)
    large = CoverageBand(quantile=0.15, realized=0.20, n=2000)

    assert small.standard_error > large.standard_error
    assert abs(small.z) < abs(large.z)
    assert small.error == pytest.approx(0.05)


def test_within_tolerance_uses_the_spec_gate():
    assert CoverageBand(0.15, 0.15 + COVERAGE_TOLERANCE - 1e-9, 100).within_tolerance
    assert not CoverageBand(0.15, 0.15 + COVERAGE_TOLERANCE + 1e-3, 100).within_tolerance


def test_standard_error_is_nan_without_observations():
    assert np.isnan(CoverageBand(0.15, float("nan"), 0).standard_error)


# ── KS ────────────────────────────────────────────────────────────────


def test_ks_accepts_uniform_samples():
    u = np.random.default_rng(1).uniform(0, 1, 5_000)
    d, p = ks_uniform(u)
    assert d < 0.03
    assert p > 0.05


def test_ks_rejects_a_clearly_non_uniform_sample():
    # Everything piled in the middle — the signature of an over-wide fan.
    u = np.random.default_rng(2).uniform(0.4, 0.6, 2_000)
    d, p = ks_uniform(u)
    assert d > 0.3
    assert p < 0.01


def test_ks_matches_a_hand_computable_case():
    """u = [0.25, 0.5, 0.75] against Uniform(0,1).

    D+ = max(i/n - x) = max(1/3-0.25, 2/3-0.5, 1-0.75) = 0.25
    D- = max(x - (i-1)/n) = max(0.25-0, 0.5-1/3, 0.75-2/3) = 0.25
    """
    d, _ = ks_uniform(np.array([0.25, 0.5, 0.75]))
    assert d == pytest.approx(0.25)


def test_ks_needs_samples():
    d, p = ks_uniform(np.array([0.5]))
    assert np.isnan(d) and np.isnan(p)


# ── the harness end to end ────────────────────────────────────────────


def test_pit_runs_and_reports_effective_n():
    bars = make_bars(n_sessions=120)
    frame = bars_to_frame(bars)
    params = PplParams(horizon_bars=10, n_paths=400, n_render_paths=400)

    res = run_pit(_service(), "SYN", frame, params, min_history=600, stride=10)

    assert res.n_effective > 20
    assert len(res.anchors) == res.n_effective
    assert res.stride == 10
    assert ((res.pit_values >= 0) & (res.pit_values <= 1)).all()


def test_stride_defaults_to_the_horizon():
    """Anchors closer than the horizon share outcome bars, so n would overstate
    the evidence."""
    frame = bars_to_frame(make_bars(n_sessions=120))
    params = PplParams(horizon_bars=13, n_paths=200, n_render_paths=200)
    res = run_pit(_service(), "SYN", frame, params, min_history=600)
    assert res.stride == 13


def test_pit_is_roughly_uniform_on_data_the_model_can_describe():
    """A driftless Gaussian walk is exactly what the zero-drift model claims the
    world is, so coverage should land near nominal. This is the positive
    control — if it fails, the harness or the model is broken."""
    frame = bars_to_frame(make_bars(n_sessions=400, sigma=0.004, drift=0.0, seed=7))
    params = PplParams(horizon_bars=13, n_paths=2_000, n_render_paths=500)

    res = run_pit(_service(), "SYN", frame, params, min_history=900, stride=13)

    assert res.n_effective >= 100
    assert res.mean_pit == pytest.approx(0.5, abs=0.12)
    for band in res.bands:
        assert abs(band.error) < 0.15, f"q{band.quantile}: {band.summary if False else band.error}"


def test_pit_detects_a_fan_that_is_far_too_narrow():
    """Shrink sigma by 5x after fitting and the realized price should fall
    outside the fan constantly — u piles up at both ends, coverage at the
    extreme quantiles blows past tolerance, KS rejects."""
    frame = bars_to_frame(make_bars(n_sessions=400, sigma=0.004, seed=11))
    params = PplParams(horizon_bars=13, n_paths=2_000, n_render_paths=500)

    class NarrowService(PplService):
        def build_from_frame(self, symbol, f, p=None):
            result = super().build_from_frame(symbol, f, p)
            if result.ok and result.snapshot and result.snapshot.terminal_sample:
                spot = result.snapshot.spot
                result.snapshot.terminal_sample = [
                    spot + (v - spot) / 5.0 for v in result.snapshot.terminal_sample
                ]
            return result

    res = run_pit(NarrowService(bars_fn=lambda s, **_: []), "SYN", frame, params,
                  min_history=900, stride=13)

    extremes = [b for b in res.bands if b.quantile in (0.15, 0.85)]
    assert not res.passed, "a 5x-too-narrow fan must not pass"
    assert any(not b.within_tolerance for b in extremes)
    assert res.ks_p < 0.05


def test_pit_detects_a_biased_fan():
    """Shift every path up 3% and the realized price lands below the fan far
    more often than it should — mean PIT collapses toward 0."""
    frame = bars_to_frame(make_bars(n_sessions=400, sigma=0.004, seed=13))
    params = PplParams(horizon_bars=13, n_paths=2_000, n_render_paths=500)

    class BiasedService(PplService):
        def build_from_frame(self, symbol, f, p=None):
            result = super().build_from_frame(symbol, f, p)
            if result.ok and result.snapshot and result.snapshot.terminal_sample:
                result.snapshot.terminal_sample = [
                    v * 1.03 for v in result.snapshot.terminal_sample
                ]
            return result

    res = run_pit(BiasedService(bars_fn=lambda s, **_: []), "SYN", frame, params,
                  min_history=900, stride=13)

    assert res.mean_pit < 0.35, f"biased fan should skew PIT low, got {res.mean_pit:.3f}"
    assert not res.passed


def test_pit_refits_per_anchor_and_never_sees_the_future():
    """Each anchor must be handed only the prefix. If the harness leaked the
    full frame, every call would receive the same length."""
    frame = bars_to_frame(make_bars(n_sessions=120))
    params = PplParams(horizon_bars=10, n_paths=100, n_render_paths=100)
    seen: list[int] = []

    class SpyService(PplService):
        def build_from_frame(self, symbol, f, p=None):
            seen.append(len(f))
            return super().build_from_frame(symbol, f, p)

    run_pit(SpyService(bars_fn=lambda s, **_: []), "SYN", frame, params,
            min_history=600, stride=10)

    assert len(set(seen)) == len(seen), "each anchor must get a distinct prefix"
    assert seen == sorted(seen), "prefixes must grow monotonically"
    assert max(seen) <= len(frame) - params.horizon_bars


def test_pit_rejects_histories_too_short_to_evaluate():
    frame = bars_to_frame(make_bars(n_sessions=20))
    with pytest.raises(ValueError, match="need"):
        run_pit(_service(), "SYN", frame, PplParams(horizon_bars=10), min_history=800)


def test_max_anchors_thins_evenly_rather_than_truncating():
    """Truncating would silently calibrate on the oldest slice only."""
    frame = bars_to_frame(make_bars(n_sessions=300))
    params = PplParams(horizon_bars=13, n_paths=100, n_render_paths=100)

    res = run_pit(_service(), "SYN", frame, params, min_history=700, stride=13,
                  max_anchors=20)

    assert res.n_effective <= 20
    span = (res.anchors[-1] - res.anchors[0]).days
    full = run_pit(_service(), "SYN", frame, params, min_history=700, stride=13)
    full_span = (full.anchors[-1] - full.anchors[0]).days
    assert span > full_span * 0.8, "thinning must preserve the window, not truncate it"


def test_to_schema_round_trips_coverage():
    frame = bars_to_frame(make_bars(n_sessions=200))
    params = PplParams(horizon_bars=13, n_paths=200, n_render_paths=200)
    res = run_pit(_service(), "SYN", frame, params, min_history=700, stride=13)

    cal = res.to_schema()
    assert cal.n_effective == res.n_effective
    assert set(cal.coverage) == {"0.15", "0.35", "0.65", "0.85"}
    assert cal.ks_stat == pytest.approx(res.ks_stat)


def test_summary_names_the_verdict():
    frame = bars_to_frame(make_bars(n_sessions=200))
    params = PplParams(horizon_bars=13, n_paths=200, n_render_paths=200)
    text = run_pit(_service(), "SYN", frame, params, min_history=700, stride=13).summary()
    assert "VERDICT:" in text
    assert "q0.15" in text


# ── regime split ──────────────────────────────────────────────────────


def test_regime_split_buckets_anchors_by_trailing_vol():
    """Pooled coverage can hide a fan that is too wide when calm and too narrow
    when violent — the two errors cancel."""
    frame = bars_to_frame(make_bars(n_sessions=400, sigma=0.004, seed=17))
    params = PplParams(horizon_bars=13, n_paths=400, n_render_paths=400)
    res = run_pit(_service(), "SYN", frame, params, min_history=900, stride=13)

    split = split_by_volatility_regime(res, frame)

    assert set(split) <= {"calm", "normal", "volatile"}
    assert split, "expected at least one populated regime bucket"
    assert sum(b[0].n for b in split.values()) == res.n_effective


def test_regime_split_is_skipped_when_there_is_too_little_data():
    frame = bars_to_frame(make_bars(n_sessions=120))
    params = PplParams(horizon_bars=10, n_paths=100, n_render_paths=100)
    res = run_pit(_service(), "SYN", frame, params, min_history=600, stride=40)
    assert split_by_volatility_regime(res, frame, lookback=60) == {}
