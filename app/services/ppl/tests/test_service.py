"""End-to-end: bars in, snapshot out — including every failure path."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.ppl.contract import PplBuilder
from app.services.ppl.schemas import MODEL_VERSION, PplParams
from app.services.ppl.service import PplService, _session_slots
from app.services.ppl.tests.conftest import (
    BARS_PER_SESSION_30M,
    bars_to_frame,
    make_bars,
)


def _service(bars) -> PplService:
    return PplService(bars_fn=lambda symbol, **_: bars)


# ── happy path ────────────────────────────────────────────────────────


def test_builds_a_complete_snapshot(bars_fn):
    result = PplService(bars_fn=bars_fn).build("SMH")
    assert result.ok, result.error

    snap = result.snapshot
    assert snap.symbol == "SMH"
    assert snap.interval == "30m"
    assert snap.spot > 0
    assert snap.model_version == MODEL_VERSION
    assert snap.horizon_end > snap.as_of
    assert snap.bars_per_session == BARS_PER_SESSION_30M


def test_path_matrix_matches_the_requested_render_shape(bars_fn):
    params = PplParams(horizon_bars=20, n_paths=1_000, n_render_paths=50)
    snap = PplService(bars_fn=bars_fn).build("SMH", params).snapshot

    assert len(snap.paths) <= 50
    assert all(len(p) == 21 for p in snap.paths)
    assert all(p[0] == pytest.approx(snap.spot, abs=1e-3) for p in snap.paths)


def test_four_levels_with_quantiles_attached(bars_fn):
    snap = PplService(bars_fn=bars_fn).build("SMH").snapshot
    assert len(snap.levels) == 4
    assert [l.quantile for l in snap.levels] == [0.15, 0.35, 0.65, 0.85]
    assert [l.price for l in snap.levels] == sorted(l.price for l in snap.levels)


def test_state_is_reported_with_its_support(bars_fn):
    snap = PplService(bars_fn=bars_fn).build("SMH").snapshot
    assert 0 <= snap.state.code < 32
    assert snap.state.label.count("|") == 2
    assert snap.state.observations >= 0
    assert isinstance(snap.state.pooled, bool)


def test_symbol_is_normalized(bars_fn):
    assert PplService(bars_fn=bars_fn).build("  smh ").snapshot.symbol == "SMH"


def test_service_satisfies_the_builder_contract(bars_fn):
    assert isinstance(PplService(bars_fn=bars_fn), PplBuilder)


# ── determinism ───────────────────────────────────────────────────────


def test_same_anchor_gives_an_identical_fan(bars_fn):
    svc = PplService(bars_fn=bars_fn)
    a, b = svc.build("SMH").snapshot, svc.build("SMH").snapshot

    assert a.seed == b.seed
    assert a.paths == b.paths
    assert [l.price for l in a.levels] == [l.price for l in b.levels]


def test_an_explicit_seed_overrides_the_derived_one(bars_fn):
    svc = PplService(bars_fn=bars_fn)
    a = svc.build("SMH", PplParams(seed=123)).snapshot
    b = svc.build("SMH", PplParams(seed=456)).snapshot
    assert a.seed == 123 and b.seed == 456
    assert a.paths != b.paths


def test_a_new_bar_changes_the_fan(bars_fn):
    """The seed keys on the anchor bar, so the chart is stable within a bar and
    refreshes when a new one closes."""
    bars = bars_fn("SMH")
    full = PplService(bars_fn=lambda s, **_: bars).build("SMH").snapshot
    one_less = PplService(bars_fn=lambda s, **_: bars[:-1]).build("SMH").snapshot
    assert full.seed != one_less.seed
    assert full.as_of != one_less.as_of


# ── model behaviour ───────────────────────────────────────────────────


def test_zero_drift_is_the_default(bars_fn):
    """Spec D9. The fan makes a volatility claim, not a directional one."""
    snap = PplService(bars_fn=bars_fn).build("SMH").snapshot
    assert snap.drift_mode == "zero"
    assert snap.drift_per_bar == 0.0


def test_zero_drift_still_reports_what_the_state_estimated(bars_fn):
    """The conditioning's claim stays inspectable — discarding it silently
    would make the mode switch invisible."""
    snap = PplService(bars_fn=bars_fn).build("SMH").snapshot
    assert snap.state_drift_per_bar != 0.0
    assert snap.state.observations > 0


def test_state_drift_mode_applies_the_state_estimate(bars_fn):
    snap = PplService(bars_fn=bars_fn).build(
        "SMH", PplParams(drift_mode="state")
    ).snapshot
    assert snap.drift_mode == "state"
    assert snap.drift_per_bar == snap.state_drift_per_bar


def test_zero_drift_centres_the_fan_on_spot(bars_fn):
    """With no drift the median terminal price sits at spot, so the fan's tilt
    can only come from fat tails and log compounding — not a forecast."""
    svc = PplService(bars_fn=bars_fn)
    zero = svc.build("SMH", PplParams(horizon_bars=40, n_paths=20_000)).snapshot
    drifted = svc.build(
        "SMH", PplParams(horizon_bars=40, n_paths=20_000, drift_mode="state")
    ).snapshot

    zero_median_offset = zero.terminal_quantiles["0.50"] / zero.spot - 1
    drifted_median_offset = drifted.terminal_quantiles["0.50"] / drifted.spot - 1

    assert abs(zero_median_offset) < 0.002
    assert abs(drifted_median_offset) > abs(zero_median_offset)


def test_horizon_widens_the_distribution(bars_fn):
    svc = PplService(bars_fn=bars_fn)
    short = svc.build("SMH", PplParams(horizon_bars=10)).snapshot
    long = svc.build("SMH", PplParams(horizon_bars=40)).snapshot
    assert long.sigma_horizon > short.sigma_horizon
    assert long.min_move > short.min_move


def test_min_move_is_derived_from_horizon_sigma(bars_fn):
    params = PplParams(min_move_sigma_mult=0.25)
    snap = PplService(bars_fn=bars_fn).build("SMH", params).snapshot
    assert snap.min_move == pytest.approx(snap.sigma_horizon * 0.25)


def test_density_method_is_selectable(bars_fn):
    snap = PplService(bars_fn=bars_fn).build(
        "SMH", PplParams(method="density")
    ).snapshot
    assert all(l.quantile is None for l in snap.levels)


def test_higher_volatility_input_widens_the_fan():
    calm = _service(make_bars(sigma=0.002)).build("X").snapshot
    wild = _service(make_bars(sigma=0.02)).build("X").snapshot
    assert wild.sigma_horizon > calm.sigma_horizon * 3


# ── failure paths ─────────────────────────────────────────────────────


def test_unsupported_interval_is_reported_not_raised(bars_fn):
    result = PplService(bars_fn=bars_fn).build("SMH", PplParams(interval="5m"))
    assert result.status == "error"
    assert "not supported" in result.error
    assert result.snapshot is None


def test_no_bars_is_reported():
    result = PplService(bars_fn=lambda s, **_: []).build("NOPE")
    assert result.status == "no_data"
    assert "NOPE" in result.error


def test_too_few_bars_is_reported_with_the_counts():
    result = _service(make_bars(n_sessions=5)).build("THIN")
    assert result.status == "insufficient_data"
    assert result.metadata["available_bars"] < result.metadata["required_bars"]
    assert result.snapshot is None


@pytest.mark.parametrize(
    "interval,max_days",
    [("30m", 300), ("1h", 500), ("4h", 1700), ("1d", 3000)],
)
def test_read_window_stays_bounded_per_interval(bars, interval, max_days):
    """The read window is sized off bars-per-TRADING-day (ET 04:00-20:00).

    Sizing it off the 6.5h regular session floors 4h to 1 bar/day and asks for
    a 6,400-day window — a full scan of a 131M-row table that took ClickHouse
    down repeatedly during P4.
    """
    seen: dict = {}

    def _spy(symbol, **kwargs):
        seen.update(kwargs)
        return bars

    PplService(bars_fn=_spy).build("SPY", PplParams(interval=interval))

    assert seen["lookback_days"] <= max_days, (
        f"{interval} asked for {seen['lookback_days']} days"
    )
    assert seen["limit"] is not None, "an explicit limit must bound the row count"


def test_staleness_is_tri_state(bars_fn):
    """`staleness_checked=False` must never be read as "the feed is fine" — when
    the check doesn't run, neither companion field carries information."""
    snap = PplService(bars_fn=bars_fn).build("SMH").snapshot
    if snap.staleness_checked:
        assert snap.stale_age_bars is not None
    else:
        assert snap.stale is False and snap.stale_age_bars is None


def test_ppl_rejects_intervals_it_has_not_been_calibrated_for(bars_fn):
    """PPL supports a subset of the platform's intervals by design (spec D1)."""
    for interval in ("1m", "5m", "15m", "2h"):
        assert PplService(bars_fn=bars_fn).build("SMH", PplParams(interval=interval)).status == "error"


# ── session geometry ──────────────────────────────────────────────────


def test_session_slots_cycle_within_the_et_session():
    frame = bars_to_frame(make_bars(n_sessions=10))
    slots, per_session = _session_slots(frame.index, "30m")

    assert per_session == BARS_PER_SESSION_30M
    assert slots[0] == 0
    assert slots[BARS_PER_SESSION_30M - 1] == BARS_PER_SESSION_30M - 1
    assert slots[BARS_PER_SESSION_30M] == 0, "a new session must restart the slot count"


def test_daily_bars_are_one_slot_per_session():
    frame = bars_to_frame(make_bars(n_sessions=30))
    slots, per_session = _session_slots(frame.index, "1d")
    assert per_session == 1
    assert np.all(slots == 0)


def test_long_sessions_fold_into_the_final_slot():
    """Real ET 04:00-20:00 coverage is ragged — an occasional session carries an
    extra bar. Slots must stay inside the time-of-day factor array; letting one
    through raised IndexError against live SPY 1h bars."""
    bars = make_bars(n_sessions=8)
    extra = bars[-1]
    padded = bars + [
        type(extra)(
            timestamp=extra.timestamp + pd.Timedelta(minutes=30 * (i + 1)),
            open=extra.close, high=extra.close, low=extra.close, close=extra.close,
        )
        for i in range(4)
    ]
    slots, per_session = _session_slots(bars_to_frame(padded).index, "30m")

    assert per_session == BARS_PER_SESSION_30M
    assert slots.max() < per_session, "no slot may index past the tod factor array"
    assert slots[-1] == per_session - 1, "overflow bars fold into the final slot"


def test_short_sessions_do_not_shift_later_slots():
    """A half day must shorten its own session, not renumber everything after
    it — the slot index is derived per ET date, not by counting from the start."""
    bars = make_bars(n_sessions=6)
    trimmed = [b for i, b in enumerate(bars) if not (13 <= i < 26 and i % 13 > 8)]
    slots, per_session = _session_slots(bars_to_frame(trimmed).index, "30m")

    assert per_session == BARS_PER_SESSION_30M, "the modal session must still win"
    assert slots[26 - 4] == 0, "the session after the short one restarts at slot 0"


# ── the fit window belongs to the model, not to the loader ────────────


def test_build_from_frame_truncates_to_the_fit_lookback():
    """The PIT harness hands in an EXPANDING prefix; production fetches a fixed
    window. If truncation lived only in the loader, calibration would measure a
    model the product never runs — at 1h the harness prefix reaches ~35,000 bars
    against production's 4,000, and a GARCH fit over that many years of regime
    changes is pushed toward spurious IGARCH.

    Regression test for exactly that: a long frame must produce the same
    snapshot as its own tail.
    """
    bars = make_bars(n_sessions=200, seed=5)          # 2,600 bars at 30m
    lookback = 1_200
    params = PplParams(interval="30m", fit_lookback_bars=lookback, n_paths=200)

    svc = PplService(bars_fn=lambda *_a, **_k: [])
    full = svc.build_from_frame("SMH", bars_to_frame(bars), params).snapshot
    tail = svc.build_from_frame(
        "SMH", bars_to_frame(bars).iloc[-lookback:], params
    ).snapshot

    assert full.n_fit_bars == lookback, "the long frame must be cut to the window"
    assert tail.n_fit_bars == lookback
    # Same window in, byte-identical model out — including the volatility fit.
    assert full.vol.sigma_next == tail.vol.sigma_next
    assert full.vol.persistence == tail.vol.persistence
    assert full.sigma_horizon == tail.sigma_horizon
    assert full.seed == tail.seed


def test_the_fetched_path_and_the_frame_path_fit_the_same_window():
    """`build()` and `build_from_frame()` must agree — they are the live route
    and the calibration route, and the gate is worthless if they diverge."""
    bars = make_bars(n_sessions=200, seed=6)
    params = PplParams(interval="30m", fit_lookback_bars=1_000, n_paths=200)

    fetched = PplService(bars_fn=lambda *_a, **_k: bars).build("SMH", params).snapshot
    handed = PplService(bars_fn=lambda *_a, **_k: []).build_from_frame(
        "SMH", bars_to_frame(bars), params
    ).snapshot

    assert fetched.n_fit_bars == handed.n_fit_bars == 1_000
    assert fetched.spot == handed.spot
    assert fetched.vol.persistence == handed.vol.persistence
    assert fetched.terminal_quantiles == handed.terminal_quantiles
