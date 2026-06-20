"""EW-7: Intraday wave alert scanner tests.

Tests the on-demand scan_intraday_alerts() and IntradayWaveScanner using
mocked compute_labeling — no live ClickHouse connection required.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.services.alerts.intraday import (
    IntradayWaveScanner,
    scan_intraday_alerts,
    INTRADAY_INTERVALS,
    _labeling_to_state,
)
from app.signals.elliott.schemas import WaveLabeling, WaveCandidate, WaveScenario
from app.indicators.pivots import Pivot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_pivot(i: int, price: float, kind: str) -> Pivot:
    return Pivot(index=i, timestamp=datetime(2025, 1, 1), price=price,
                 kind=kind, k=5, degree=1, confirmed_at_index=i + 5)


def _mk_candidate(direction="up", current_wave="3", confidence=0.72,
                  probability=0.65, stop=140.0,
                  targets=None) -> WaveCandidate:
    # Default: entry=155, stop=140 → risk=15; target=200 → reward=45, R:R=3.0
    pivots = [_mk_pivot(0, 100.0, "low"), _mk_pivot(10, 150.0, "high"),
              _mk_pivot(20, 120.0, "low")]
    return WaveCandidate(
        structure="impulse", direction=direction, current_wave=current_wave,
        degree=1, pivots=pivots, labels=["0", "1", "2"],
        rules_passed={"w2_no_overlap": True}, rule_score=1.0,
        fib_score=0.80, confidence=confidence, probability=probability,
        invalidation_price=stop,
        fib_targets=targets or {"w3=1.618xW1": 200.0},
        rationale="In wave 3 of an up impulse. Stop 140.0. Target 200.0.",
    )


def _mk_labeling(symbol="AAPL", interval="5m", primary=None,
                 as_of_price=155.0) -> WaveLabeling:
    return WaveLabeling(
        symbol=symbol, interval=interval,
        as_of=datetime(2025, 6, 19, 14, 30), as_of_index=500,
        as_of_price=as_of_price, n_confirmed_swings=3,
        primary=primary, engine_ver="ew3.8.0",
    )


# ---------------------------------------------------------------------------
# scan_intraday_alerts
# ---------------------------------------------------------------------------

def test_scan_returns_alert_when_count_passes_gates():
    """A wave-3 up impulse with high probability and good R:R surfaces as an alert."""
    # entry=155, stop=140 → risk=15; target=200 → reward=45, R:R=3.0 ≥ 2.0
    cand = _mk_candidate(direction="up", current_wave="3",
                         probability=0.70, stop=140.0,
                         targets={"w3=1.618xW1": 200.0})
    lab = _mk_labeling(primary=cand, as_of_price=155.0)

    with patch("app.services.alerts.intraday.compute_labeling", return_value=lab):
        alerts = scan_intraday_alerts(["AAPL"], "5m")

    assert len(alerts) == 1
    assert alerts[0].symbol == "AAPL"
    assert alerts[0].current_wave == "3"
    assert alerts[0].direction == "long"
    assert alerts[0].risk_reward >= 2.0


def test_scan_filters_low_probability():
    """Count with probability below gate is excluded."""
    cand = _mk_candidate(probability=0.40, stop=100.0,
                         targets={"w3=1.618xW1": 300.0})
    lab = _mk_labeling(primary=cand, as_of_price=155.0)

    with patch("app.services.alerts.intraday.compute_labeling", return_value=lab):
        alerts = scan_intraday_alerts(["AAPL"], "5m", min_probability=0.6)

    assert alerts == []


def test_scan_filters_poor_rr():
    """Count with R:R below gate is excluded."""
    # stop=150 entry=155 → risk=5; target=160 → reward=5 → R:R=1.0 < 2.0
    cand = _mk_candidate(probability=0.70, stop=150.0,
                         targets={"w3=1.618xW1": 160.0})
    lab = _mk_labeling(primary=cand, as_of_price=155.0)

    with patch("app.services.alerts.intraday.compute_labeling", return_value=lab):
        alerts = scan_intraday_alerts(["AAPL"], "5m")

    assert alerts == []


def test_scan_skips_corrective_waves():
    """Corrective wave (zigzag) primary does not generate an alert."""
    cand = _mk_candidate(probability=0.70, stop=100.0,
                         targets={"C=1.618xA": 200.0})
    cand.structure = "zigzag"
    cand.current_wave = "C"
    lab = _mk_labeling(primary=cand, as_of_price=155.0)

    with patch("app.services.alerts.intraday.compute_labeling", return_value=lab):
        alerts = scan_intraday_alerts(["AAPL"], "5m")

    assert alerts == []


def test_scan_none_labeling_skipped():
    """compute_labeling returning None (no usable bars) is handled gracefully."""
    with patch("app.services.alerts.intraday.compute_labeling", return_value=None):
        alerts = scan_intraday_alerts(["AAPL", "TSLA"], "5m")
    assert alerts == []


def test_scan_exception_skipped():
    """An exception from compute_labeling for one symbol doesn't kill the scan."""
    # entry=200, stop=180 → risk=20; target=300 → reward=100, R:R=5.0 ≥ 2.0
    good_cand = _mk_candidate(probability=0.70, stop=180.0,
                              targets={"w3=1.618xW1": 300.0})
    good_lab = _mk_labeling(symbol="TSLA", primary=good_cand, as_of_price=200.0)

    def _side_effect(symbol, interval, source=None):
        if symbol == "AAPL":
            raise RuntimeError("CH timeout")
        return good_lab

    with patch("app.services.alerts.intraday.compute_labeling",
               side_effect=_side_effect):
        alerts = scan_intraday_alerts(["AAPL", "TSLA"], "5m")

    assert len(alerts) == 1
    assert alerts[0].symbol == "TSLA"


def test_scan_sorted_by_probability_descending():
    """Multiple alerts are returned highest-probability first."""
    def _make_lab(sym, prob, stop, target):
        c = _mk_candidate(probability=prob, stop=stop, targets={"t": target})
        return _mk_labeling(symbol=sym, primary=c, as_of_price=155.0)

    labs = {
        "AAPL": _make_lab("AAPL", 0.62, 100.0, 300.0),
        "TSLA": _make_lab("TSLA", 0.78, 100.0, 300.0),
        "NVDA": _make_lab("NVDA", 0.65, 100.0, 300.0),
    }

    with patch("app.services.alerts.intraday.compute_labeling",
               side_effect=lambda sym, *a, **kw: labs.get(sym)):
        alerts = scan_intraday_alerts(["AAPL", "TSLA", "NVDA"], "5m")

    probs = [a.probability for a in alerts]
    assert probs == sorted(probs, reverse=True)


# ---------------------------------------------------------------------------
# IntradayWaveScanner debounce
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scanner_fires_on_first_bar():
    """First bar that produces a qualifying alert should fire the callback."""
    cand = _mk_candidate(probability=0.70, stop=100.0,
                         targets={"w3=1.618xW1": 300.0})
    lab = _mk_labeling(symbol="AAPL", primary=cand, as_of_price=155.0)

    fired = []
    scanner = IntradayWaveScanner(["AAPL"], "5m", broadcast_cb=fired.append)

    bar = MagicMock()
    bar.symbol = "AAPL"

    with patch("app.services.alerts.intraday.compute_labeling", return_value=lab):
        await scanner.on_bar(bar)

    assert len(fired) == 1


@pytest.mark.asyncio
async def test_scanner_debounces_same_wave():
    """Repeated bars with the same wave/direction fire the callback only once."""
    cand = _mk_candidate(probability=0.70, stop=100.0,
                         targets={"w3=1.618xW1": 300.0})
    lab = _mk_labeling(symbol="AAPL", primary=cand, as_of_price=155.0)

    fired = []
    scanner = IntradayWaveScanner(["AAPL"], "5m", broadcast_cb=fired.append)

    bar = MagicMock()
    bar.symbol = "AAPL"

    with patch("app.services.alerts.intraday.compute_labeling", return_value=lab):
        await scanner.on_bar(bar)
        await scanner.on_bar(bar)
        await scanner.on_bar(bar)

    assert len(fired) == 1  # debounced — same wave+direction


@pytest.mark.asyncio
async def test_scanner_refires_on_wave_change():
    """When the wave changes (e.g. 3→5), the scanner fires again."""
    cand3 = _mk_candidate(current_wave="3", probability=0.70, stop=100.0,
                          targets={"w3=1.618xW1": 300.0})
    cand5 = _mk_candidate(current_wave="5", probability=0.68, stop=130.0,
                          targets={"w5=1.0xW1": 350.0})
    lab3 = _mk_labeling(symbol="AAPL", primary=cand3, as_of_price=155.0)
    lab5 = _mk_labeling(symbol="AAPL", primary=cand5, as_of_price=200.0)

    fired = []
    scanner = IntradayWaveScanner(["AAPL"], "5m", broadcast_cb=fired.append)

    bar = MagicMock()
    bar.symbol = "AAPL"

    with patch("app.services.alerts.intraday.compute_labeling", return_value=lab3):
        await scanner.on_bar(bar)

    with patch("app.services.alerts.intraday.compute_labeling", return_value=lab5):
        await scanner.on_bar(bar)

    assert len(fired) == 2
    assert fired[0].current_wave == "3"
    assert fired[1].current_wave == "5"


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_intraday_intervals_set():
    assert "5m" in INTRADAY_INTERVALS
    assert "15m" in INTRADAY_INTERVALS
    assert "1d" not in INTRADAY_INTERVALS


def test_labeling_to_state_fields():
    cand = _mk_candidate()
    lab = _mk_labeling(primary=cand, as_of_price=155.0)
    state = _labeling_to_state(lab)
    assert state.symbol == "AAPL"
    assert state.as_of_price == 155.0
    assert state.primary is not None
    assert state.primary.structure == "impulse"
    assert state.primary.current_wave == "3"
