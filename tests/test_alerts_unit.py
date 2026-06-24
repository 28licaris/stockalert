"""EW-6: WaveAlert builder + gates (no AWS)."""
from __future__ import annotations

import datetime as dt

from app.services.alerts import build_alert, scan_alerts
from app.services.readers.wave_reader import WaveCountView, WaveStateResponse


def _state(*, wave="3", direction="up", price=270.0, stop=265.0,
           targets=None, prob=0.7, structure="impulse", interval="1d"):
    primary = WaveCountView(
        structure=structure, direction=direction, current_wave=wave, degree=0,
        probability=prob, confidence=prob, invalidation=stop,
        targets=targets if targets is not None else {"w3=1.618xW1": 320.0, "w3=2.618xW1": 350.0},
        rationale="r", pivots=[],
    )
    return WaveStateResponse(
        symbol="AAPL", interval=interval, asset_class="equity",
        as_of_date=dt.date(2026, 5, 27), as_of_price=price, primary=primary,
        uncertainty=0.1, engine_ver="ew2.0.0", source="store",
    )


def test_build_alert_long_risk_reward():
    a = build_alert(_state(price=270.0, stop=265.0))  # risk 5, reward to 320 = 50 → rr 10
    assert a is not None
    assert a.direction == "long"
    assert a.trade_type == "swing"
    assert a.stop == 265.0 and a.entry == 270.0
    assert a.target_1 == 320.0 and a.target_2 == 350.0
    assert a.risk_reward == 10.0


def test_build_alert_day_trade_interval():
    a = build_alert(_state(interval="15m"))
    assert a is not None and a.trade_type == "day"


def test_build_alert_short_direction():
    a = build_alert(_state(direction="down", price=270.0, stop=275.0,
                           targets={"w3=1.618xW1": 240.0}))
    assert a is not None and a.direction == "short"
    assert a.risk_reward == round(30 / 5, 2)


def test_no_alert_when_target_behind_price():
    # price already past the only target → no forward target → no alert
    assert build_alert(_state(price=330.0, targets={"w3=1.618xW1": 320.0})) is None


def test_no_alert_for_non_entry_wave():
    assert build_alert(_state(wave="complete", structure="zigzag")) is None


class _FakeReader:
    def __init__(self, states):
        self._states = states

    def list_latest(self, interval="1d"):
        return self._states


def test_scan_gates_on_probability_and_rr():
    good = _state(price=270.0, stop=265.0, prob=0.7)          # rr 10, prob .7 → pass
    low_prob = _state(price=270.0, stop=265.0, prob=0.4)       # prob too low
    low_rr = _state(price=318.0, stop=265.0, prob=0.7)         # rr ~0.04 → fail
    alerts = scan_alerts(reader=_FakeReader([good, low_prob, low_rr]))
    assert len(alerts) == 1
    assert alerts[0].probability == 0.7
