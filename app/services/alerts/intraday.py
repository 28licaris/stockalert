"""EW-7: Intraday wave alert scanner.

Two complementary paths:

  scan_intraday_alerts(symbols, interval)
    On-demand: calls compute_labeling(source=AUTO) for each symbol — bars come
    from ClickHouse (hot cache, sub-100ms) with lake fallback.  Used by the
    HTTP endpoint and the MCP tool.

  IntradayWaveScanner
    Live subscription: wraps a provider's subscribe_bars() callback; on each
    incoming bar it re-runs compute_labeling for that symbol and fires the
    broadcast_cb if a new alert clears the gates.  Debounced per symbol: only
    fires when the active wave or direction changes (not on every tick).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Callable, Optional

from app.services.alerts.schemas import WaveAlert
from app.services.alerts.service import build_alert, _ENTRY_WAVES
from app.services.elliott_store.recompute import compute_labeling
from app.services.readers.bars_gateway import BarSource
from app.signals.elliott.schemas import WaveLabeling

logger = logging.getLogger(__name__)

# Intraday intervals eligible for live EW scanning
INTRADAY_INTERVALS = {"5m", "15m", "30m", "1h"}

# Alert gates (same as EW-6 swing scanner)
_MIN_PROBABILITY = 0.6
_MIN_RISK_REWARD = 2.0


def scan_intraday_alerts(
    symbols: list[str],
    interval: str = "5m",
    *,
    min_probability: float = _MIN_PROBABILITY,
    min_risk_reward: float = _MIN_RISK_REWARD,
) -> list[WaveAlert]:
    """On-demand intraday scan — bars sourced from ClickHouse (AUTO).

    Computes a fresh WaveLabeling for each symbol at the requested interval,
    applies build_alert() + probability/R:R gates, and returns passing alerts
    sorted by probability descending.
    """
    alerts: list[WaveAlert] = []
    for sym in symbols:
        try:
            lab = compute_labeling(sym, interval, source=BarSource.AUTO)
        except Exception:
            logger.exception("compute_labeling failed for %s@%s", sym, interval)
            continue
        if lab is None:
            continue
        # Adapt WaveLabeling → the WaveStateResponse shape build_alert expects
        state = _labeling_to_state(lab)
        alert = build_alert(state)
        if alert is None:
            continue
        if alert.probability >= min_probability and alert.risk_reward >= min_risk_reward:
            alerts.append(alert)

    alerts.sort(key=lambda a: (-a.probability, -a.risk_reward))
    logger.info("scan_intraday_alerts(%s, %d symbols): %d alerts", interval, len(symbols), len(alerts))
    return alerts


class IntradayWaveScanner:
    """Live bar subscriber that fires wave alerts when a new setup forms.

    Usage::

        scanner = IntradayWaveScanner(["AAPL", "TSLA"], "5m",
                                      broadcast_cb=my_ws_broadcast)
        provider.subscribe_bars(scanner.on_bar, ["AAPL", "TSLA"])

    Debounce: one alert per (symbol, wave, direction) until the count changes.
    """

    def __init__(
        self,
        symbols: list[str],
        interval: str = "5m",
        *,
        broadcast_cb: Optional[Callable[[WaveAlert], None]] = None,
        min_probability: float = _MIN_PROBABILITY,
        min_risk_reward: float = _MIN_RISK_REWARD,
    ) -> None:
        self.symbols = symbols
        self.interval = interval
        self.broadcast_cb = broadcast_cb
        self.min_probability = min_probability
        self.min_risk_reward = min_risk_reward
        # debounce: last fired (wave, direction) per symbol
        self._last_fired: dict[str, tuple[str, str]] = {}

    async def on_bar(self, bar) -> None:
        """Async callback — wire into provider.subscribe_bars().

        compute_labeling() does ClickHouse IO so we run it in a thread pool
        to avoid blocking the event loop.
        """
        symbol = getattr(bar, "symbol", None) or getattr(bar, "ticker", None)
        if symbol not in self.symbols:
            return
        try:
            lab = await asyncio.to_thread(
                compute_labeling, symbol, self.interval, source=BarSource.AUTO,
            )
        except Exception:
            logger.exception("IntradayWaveScanner: compute_labeling failed for %s", symbol)
            return
        if lab is None or lab.primary is None:
            return

        state = _labeling_to_state(lab)
        alert = build_alert(state)
        if alert is None:
            return
        if alert.probability < self.min_probability or alert.risk_reward < self.min_risk_reward:
            return

        # Debounce — skip if this exact (wave, direction) already fired
        key = (alert.current_wave, alert.direction)
        if self._last_fired.get(symbol) == key:
            return
        self._last_fired[symbol] = key

        logger.info("IntradayWaveScanner: alert %s %s@%s wave%s R:R=%.1f",
                    alert.direction, symbol, self.interval, alert.current_wave, alert.risk_reward)
        if self.broadcast_cb:
            result = self.broadcast_cb(alert)
            if inspect.isawaitable(result):
                await result


# ---------------------------------------------------------------------------
# Adapter: WaveLabeling → the dict-like object build_alert() expects
# ---------------------------------------------------------------------------

class _PrimaryView:
    """Minimal projection of WaveCandidate fields build_alert() reads."""
    def __init__(self, lab: WaveLabeling) -> None:
        p = lab.primary
        self.structure = p.structure if p else None
        self.current_wave = p.current_wave if p else None
        self.direction = p.direction if p else None
        self.invalidation = p.invalidation_price if p else None
        self.targets = p.fib_targets if p else {}
        self.probability = p.probability if p else 0.0
        self.rationale = p.rationale if p else ""


class _StateView:
    """Minimal projection of WaveStateResponse that build_alert() reads."""
    def __init__(self, lab: WaveLabeling) -> None:
        self.symbol = lab.symbol
        self.interval = lab.interval
        self.asset_class = _infer_asset_class(lab.symbol)
        self.as_of_price = lab.as_of_price
        self.as_of_date = lab.as_of.date() if lab.as_of else None
        self.primary = _PrimaryView(lab) if lab.primary else None


def _labeling_to_state(lab: WaveLabeling) -> _StateView:
    return _StateView(lab)


def _infer_asset_class(symbol: str) -> str:
    return "futures" if symbol.startswith("/") else "equities"
