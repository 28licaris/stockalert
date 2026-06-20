"""V3-2: forward projection — the next-wave confluence zone + invalidation."""
from __future__ import annotations

from app.signals.elliott.forward import project_forward


def test_wave4_up_projects_wave5_up_above_wave3():
    f = project_forward([100, 120, 108, 150], "up", "impulse", "4")
    assert "wave 5 up" in f["next_move"]
    assert f["target_low"] > 150            # wave 5 extends beyond wave-3 high
    assert f["invalidation"] == 120         # wave-1 territory (rule 3)
    assert len(f["target_basis"]) >= 2      # confluence


def test_wave4_down_projects_wave5_down_below_wave3():
    f = project_forward([200, 180, 192, 150], "down", "impulse", "4")
    assert "wave 5 down" in f["next_move"]
    assert f["target_high"] < 150


def test_wave3_target_is_forward_and_above():
    f = project_forward([100, 120, 108], "up", "impulse", "3")
    assert f["target_low"] > 108
    assert f["invalidation"] == 108


def test_complete_projects_corrective_retrace():
    f = project_forward([100, 120, 108, 150, 134, 160], "up", "impulse", "complete")
    assert "correction" in f["next_move"]
    assert f["target_low"] < 160            # retraces back into the impulse


def test_zigzag_and_short_inputs_return_none():
    assert project_forward([100, 120, 108], "up", "zigzag", "C") is None
    assert project_forward([100], "up", "impulse", "1") is None
