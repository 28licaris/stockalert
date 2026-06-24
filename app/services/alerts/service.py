"""Alert generation — pure functions over wave state + a universe scan.

`build_alert` turns one WaveStateResponse into a WaveAlert trade plan (or None
if the count isn't a tradeable entry). `scan_alerts` runs it across the latest
stored counts and applies the probability + risk:reward gates.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.alerts.schemas import WaveAlert
from app.services.readers.wave_reader import WaveReader, WaveStateResponse

logger = logging.getLogger(__name__)

# Trend-continuation entries only for v1 (wave 3 / wave 5 of an impulse).
_ENTRY_WAVES = {"3", "5"}
_SWING_INTERVALS = {"1d", "1h"}


def _trade_type(interval: str) -> str:
    return "swing" if interval in _SWING_INTERVALS else "day"


def build_alert(state: WaveStateResponse) -> Optional[WaveAlert]:
    """Build a trade plan from a state's primary count. None if not tradeable."""
    p = state.primary
    if p is None or p.structure != "impulse" or p.current_wave not in _ENTRY_WAVES:
        return None
    entry = state.as_of_price
    stop = p.invalidation
    if entry is None or stop is None or not p.targets:
        return None

    long = p.direction == "up"
    # forward targets only (above entry for long, below for short), nearest first
    fwd = sorted(
        (v for v in p.targets.values() if (v - entry) * (1 if long else -1) > 0),
        key=lambda v: abs(v - entry),
    )
    if not fwd:
        return None
    target_1 = fwd[0]
    target_2 = fwd[1] if len(fwd) > 1 else None

    risk = (entry - stop) if long else (stop - entry)
    reward = (target_1 - entry) if long else (entry - target_1)
    if risk <= 0:
        return None
    rr = round(reward / risk, 2)

    return WaveAlert(
        symbol=state.symbol, asset_class=state.asset_class, interval=state.interval,
        setup=f"wave{p.current_wave}_entry",
        direction="long" if long else "short", trade_type=_trade_type(state.interval),
        probability=p.probability, entry=round(entry, 2), stop=round(stop, 2),
        target_1=round(target_1, 2), target_2=round(target_2, 2) if target_2 else None,
        risk_reward=rr, current_wave=p.current_wave, as_of_date=state.as_of_date,
        rationale=p.rationale,
    )


def scan_alerts(interval: str = "1d", *, min_probability: float = 0.6,
                min_risk_reward: float = 2.0,
                reader: Optional[WaveReader] = None) -> list[WaveAlert]:
    """Scan the latest stored counts across the universe; return alerts passing
    the probability + risk:reward gates, sorted by probability."""
    reader = reader or WaveReader.from_settings()
    states = reader.list_latest(interval)
    alerts: list[WaveAlert] = []
    for st in states:
        a = build_alert(st)
        if a is None:
            continue
        if a.probability >= min_probability and a.risk_reward >= min_risk_reward:
            alerts.append(a)
    alerts.sort(key=lambda a: (-a.probability, -a.risk_reward))
    logger.info("scan_alerts(%s): %d/%d states passed gates", interval, len(alerts), len(states))
    return alerts
