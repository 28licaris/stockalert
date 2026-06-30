"""
Moving-average timeframe contract tests.

These pin the two numerical properties the cross-timeframe MA engine
(Phase 1 — `IndicatorReader` source-aggregation support) structurally
depends on. They are not "does SMA compute" tests (those live in
`test_sim_unit.py` / `test_indicators_ta3.py`); they are the invariants
that justify the engine's design and must not silently regress:

  1. **SMA is slice-invariant after warmup.** An N-bar SMA value at a
     given bar depends only on the trailing N closes. So a "200-day SMA"
     value is identical whether computed from exactly 200 prior bars or
     from years of history then sliced. → The engine may fetch the
     minimal `length` source bars per output point without drift.

  2. **EMA is NOT slice-invariant — the seed drifts.** `ewm(adjust=False)`
     reseeds its recursion to the first bar it is handed, and the error
     decays as `(1 - alpha)^n`. Computing an EMA on a bare display window
     gives a wrong value; feeding extra warmup history before the window
     drives the error toward zero. → The engine MUST extend the fetch
     window backward for EMA, and the amount of warmup it adds is a real
     correctness parameter, not a nicety.

If these break, the cross-timeframe overlays and the MA-crossover alerts
that read the same numbers are silently wrong. See `app/indicators/ema.py`
"Seed continuity" note.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.indicators.ema import EMA
from app.indicators.registry import get_indicator
from app.indicators.sma import SMA


def _ramp(n: int, *, start: float = 100.0, step: float = 0.7) -> pd.Series:
    """Deterministic monotone close series — no RNG, reproducible asserts."""
    return pd.Series([start + i * step for i in range(n)], dtype=float)


# ─────────────────────────────────────────────────────────────────────
# Property 1 — SMA slice-invariance after warmup
# ─────────────────────────────────────────────────────────────────────


def test_sma_value_is_slice_invariant_after_warmup() -> None:
    """
    SMA(period) at bar T == SMA(period) recomputed over only the trailing
    `period` bars ending at T. The "200-day SMA on a 5m chart" value does
    not depend on how much history preceded the window.
    """
    period = 20
    full = _ramp(400)
    sma_full = SMA(period=period).compute(full)

    # Recompute over the minimal trailing window for several anchor bars.
    for t in (period - 1, 100, 250, len(full) - 1):
        window = full.iloc[t - period + 1 : t + 1]
        sma_window = SMA(period=period).compute(window)
        # Last value of the minimal window is the only fully-formed one.
        assert sma_window.iloc[-1] == pytest.approx(sma_full.iloc[t], rel=0, abs=1e-12)


def test_sma_warmup_boundary_is_exact() -> None:
    """First `period - 1` values NaN; the `period`-th is the first finite one."""
    period = 20
    sma = SMA(period=period).compute(_ramp(60))
    assert sma.iloc[: period - 1].isna().all()
    assert np.isfinite(sma.iloc[period - 1])


# ─────────────────────────────────────────────────────────────────────
# Property 2 — EMA seed drift / continuity
# ─────────────────────────────────────────────────────────────────────


def test_ema_bare_window_drifts_from_full_history() -> None:
    """
    Computing EMA on a bare display window (no warmup) gives a DIFFERENT
    value than the true full-history EMA at the same bar. This is the bug
    the engine's warmup-extension exists to prevent — pin that it is real.
    """
    period = 20
    full = _ramp(500)
    ema_full = EMA(period=period).compute(full)

    window_len = 40  # a "zoomed in" display window, < convergence depth
    bare_window = full.iloc[-window_len:]
    ema_bare = EMA(period=period).compute(bare_window)

    # At the window's first fully-formed bar the seed error is large.
    true_at_window_start = ema_full.iloc[-window_len + period - 1]
    bare_at_window_start = ema_bare.iloc[period - 1]
    assert abs(bare_at_window_start - true_at_window_start) > 1e-6


def test_ema_warmup_extension_drives_error_to_zero() -> None:
    """
    More warmup history before the window → strictly less seed error.
    Pins that extending the fetch window is the correct fix and that a
    few multiples of `period` is enough to converge to float tolerance.
    """
    period = 20
    full = _ramp(500)
    ema_full = EMA(period=period).compute(full)
    target_idx = len(full) - 1
    true_value = ema_full.iloc[target_idx]

    errors = []
    for warmup_mult in (1, 3, 6, 10):
        warmup = period * warmup_mult
        start = target_idx - warmup + 1
        ema_ext = EMA(period=period).compute(full.iloc[start : target_idx + 1])
        errors.append(abs(ema_ext.iloc[-1] - true_value))

    # Monotonically non-increasing error as warmup grows.
    for earlier, later in zip(errors, errors[1:]):
        assert later <= earlier + 1e-15
    # By ~10x period the seed contribution is below float-display tolerance.
    assert errors[-1] < 1e-6


# ─────────────────────────────────────────────────────────────────────
# Cross-surface parity — registry path == direct construction
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name,cls", [("sma", SMA), ("ema", EMA)])
def test_registry_matches_direct_construction(name: str, cls: type) -> None:
    """
    The registry path (used by IndicatorReader / alerts / backtester) must
    produce identical values to direct construction — the "same math
    everywhere" guarantee that lets a CH-SQL bulk reader, if ever added,
    be validated against these exact vectors.
    """
    closes = _ramp(120)
    via_registry = get_indicator(name, period=30).compute(closes)
    direct = cls(period=30).compute(closes)
    pd.testing.assert_series_equal(via_registry, direct)
