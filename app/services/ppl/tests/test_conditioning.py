"""Conditioning state — 32 bins from PSAR × TSI × MACD."""
from __future__ import annotations

import pytest

from app.services.ppl.conditioning import N_STATES, compute_state_frame, state_label
from app.services.ppl.schemas import ConditioningParams


def test_state_codes_are_in_range_or_undefined(frame):
    states = compute_state_frame(frame["close"], frame["high"], frame["low"])
    codes = states["code"]
    assert codes.between(-1, N_STATES - 1).all()
    assert (codes >= 0).any(), "at least some bars must resolve to a defined state"


def test_warmup_bars_are_undefined_not_state_zero(frame):
    """PSAR's seed bars have no state. Binning them into 0 would pollute the
    drift estimate for a real state with meaningless observations."""
    states = compute_state_frame(frame["close"], frame["high"], frame["low"])
    assert states["code"].iloc[0] == -1
    assert (states["code"].iloc[:2] == -1).all()
    assert states["code"].iloc[100] >= 0


def test_state_label_round_trips_every_code():
    labels = {state_label(c) for c in range(N_STATES)}
    assert len(labels) == N_STATES, "each code must have a distinct label"
    for c in range(N_STATES):
        assert state_label(c).count("|") == 2


@pytest.mark.parametrize("bad", [-1, N_STATES, 99])
def test_state_label_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        state_label(bad)


def test_components_are_present_for_explainability(frame):
    states = compute_state_frame(frame["close"], frame["high"], frame["low"])
    for col in ("psar", "tsi", "tsi_signal", "macd_hist", "code"):
        assert col in states.columns
    assert states.index.equals(frame.index)


def test_params_change_the_state_series(frame):
    fast = compute_state_frame(
        frame["close"], frame["high"], frame["low"],
        ConditioningParams(psar_af_start=0.02, psar_af_max=0.5),
    )
    slow = compute_state_frame(
        frame["close"], frame["high"], frame["low"],
        ConditioningParams(psar_af_start=0.01, psar_af_max=0.05),
    )
    assert not fast["code"].equals(slow["code"])


def test_conditioning_is_causal(frame):
    """State at bar t must not move when later bars arrive."""
    full = compute_state_frame(frame["close"], frame["high"], frame["low"])
    cut = len(frame) // 2
    partial = compute_state_frame(
        frame["close"].iloc[:cut], frame["high"].iloc[:cut], frame["low"].iloc[:cut]
    )
    assert full["code"].iloc[:cut].equals(partial["code"])
