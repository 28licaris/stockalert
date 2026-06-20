"""V3-3: labeled alternates with hard-gate scenario output.

Each surfaced count (primary + secondary + alternates) carries:
  - confirms_at   : the price at which this count becomes active (prev count's stop)
  - scenario_label: "Primary" / "Secondary" / "Alternate 1" / …
  - WaveScenario  : rich trader-facing struct on WaveLabeling.scenarios

The hard gate: a count "flips" to the next when the current count's
invalidation_price is breached — deterministic, binary, no fuzzy logic.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.indicators.pivots import Pivot
from app.signals.elliott.engine import WaveEngine
from app.signals.elliott.schemas import WaveLabeling, WaveScenario


def _mk(i: int, price: float, kind: str, degree: int = 1) -> Pivot:
    return Pivot(index=i, timestamp=datetime(2025, 1, 1) + timedelta(days=i),
                 price=price, kind=kind, k=10, degree=degree,
                 confirmed_at_index=i + 10)


# ---------------------------------------------------------------------------
# Two-count setup: impulse primary + zigzag secondary (opposite degree/structure)
# Pivots: low→high→low→high→low (down impulse, wave 5 in progress)
# ---------------------------------------------------------------------------
_PIVOTS_5W = [
    _mk(0, 500.0, "high"),
    _mk(20, 300.0, "low"),
    _mk(40, 420.0, "high"),
    _mk(60, 220.0, "low"),
    _mk(80, 340.0, "high"),
]
_ENGINE = WaveEngine()


def _label_5w(last_price: float) -> WaveLabeling:
    return _ENGINE.label(
        _PIVOTS_5W, last_price, symbol="TEST", interval="1d",
        as_of_index=90, as_of=datetime(2025, 4, 11),
    )


def test_primary_confirms_at_is_none():
    """Primary count is already active — confirms_at must be None."""
    lab = _label_5w(280.0)
    assert lab.primary is not None
    assert lab.primary.confirms_at is None


def test_primary_scenario_label():
    lab = _label_5w(280.0)
    assert lab.primary is not None
    assert lab.primary.scenario_label == "Primary"


def test_secondary_confirms_at_equals_primary_stop():
    """Secondary.confirms_at == primary.invalidation_price (the flip trigger)."""
    lab = _label_5w(280.0)
    if lab.secondary is None:
        return  # no secondary surfaced — skip
    assert lab.secondary.confirms_at == lab.primary.invalidation_price


def test_secondary_scenario_label():
    lab = _label_5w(280.0)
    if lab.secondary is None:
        return
    assert lab.secondary.scenario_label == "Secondary"


def test_alternate_scenario_label():
    """Alternates beyond secondary get 'Alternate N' labels."""
    lab = _label_5w(280.0)
    for i, alt in enumerate(lab.alternates):
        assert alt.scenario_label == f"Alternate {i + 1}", (
            f"expected 'Alternate {i + 1}', got {alt.scenario_label!r}"
        )


def test_scenarios_list_length_matches_surfaced_counts():
    """scenarios list = 1 (primary) + 1 (secondary if present) + N alternates."""
    lab = _label_5w(280.0)
    expected = sum(1 for c in [lab.primary, lab.secondary] + lab.alternates if c)
    assert len(lab.scenarios) == expected


def test_scenarios_ranked_in_order():
    """scenarios[0].rank == 1, scenarios[1].rank == 2, etc."""
    lab = _label_5w(280.0)
    for i, sc in enumerate(lab.scenarios):
        assert sc.rank == i + 1


def test_scenarios_primary_what_confirms_text():
    """Primary scenario says 'Currently primary' (it's already active)."""
    lab = _label_5w(280.0)
    if not lab.scenarios:
        return
    assert "primary" in lab.scenarios[0].what_confirms.lower()
    assert "effect" in lab.scenarios[0].what_confirms.lower()


def test_scenarios_secondary_what_confirms_references_primary_stop():
    """Secondary scenario text references the primary's invalidation_price."""
    lab = _label_5w(280.0)
    if len(lab.scenarios) < 2 or lab.primary is None:
        return
    stop_str = str(lab.primary.invalidation_price)
    assert stop_str in lab.scenarios[1].what_confirms, (
        f"expected stop {stop_str} in: {lab.scenarios[1].what_confirms!r}"
    )


def test_scenarios_what_invalidates_references_own_stop():
    """Each scenario's what_invalidates mentions its own invalidation_price."""
    lab = _label_5w(280.0)
    for sc in lab.scenarios:
        stop_str = str(sc.invalidation)
        assert stop_str in sc.what_invalidates, (
            f"rank={sc.rank}: stop {stop_str} not in {sc.what_invalidates!r}"
        )


def test_scenarios_wave_scenario_type():
    """All items in scenarios are WaveScenario instances."""
    lab = _label_5w(280.0)
    for sc in lab.scenarios:
        assert isinstance(sc, WaveScenario)


def test_no_scenarios_when_uncertainty_is_full():
    """When no count meets min_confidence, scenarios is empty."""
    # Only 3 pivots and a price that violates leg direction → both candidates fail
    # Use a degenerate pivot set: zero-size wave-1 (never produces valid candidate)
    from app.signals.elliott.engine import WaveEngine
    tiny_engine = WaveEngine(min_confidence=0.99)  # impossibly high floor
    pivs = [
        _mk(0, 100.0, "low"),
        _mk(10, 140.0, "high"),
        _mk(20, 110.0, "low"),
    ]
    lab = tiny_engine.label(pivs, last_price=105.0, symbol="T", interval="1d",
                            as_of_index=30, as_of=datetime(2025, 1, 31))
    assert lab.primary is None
    assert lab.scenarios == []
