"""MCP tools for Elliott Wave state — agent-facing surface.

Thin adapters over `WaveReader`. Same Pydantic shapes as the HTTP routes in
`app/api/routes_wave.py`. An agent reasoning about a symbol should prefer these
over hand-deriving wave counts — the engine enforces no-look-ahead, confidence,
and invalidation. When the tool returns no primary count (uncertainty high),
that is a real "no clear count" answer, not a gap to fill in.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.alerts import WaveAlert, scan_alerts, scan_intraday_alerts
from app.services.readers.wave_reader import WaveReader, WaveStateResponse

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _reader() -> WaveReader:
    return WaveReader.from_settings()


@mcp.tool()
def get_wave_state(symbol: str, interval: str = "1d", backend: str = "auto") -> WaveStateResponse:
    """Current Elliott Wave count for a symbol.

    USE WHEN: an agent needs the wave structure of a security — "what wave is
    AAPL in?", "is NVDA's count bullish?", "where's the invalidation on TSLA?".
    Returns the primary and secondary counts, each with a structure
    (impulse/zigzag), direction, current wave, confidence, normalized
    probability, invalidation price, and Fibonacci targets — plus an
    `uncertainty` mass. If `primary` is null / uncertainty is high, there is no
    clear count; say so rather than inventing one.

    Args:
        symbol: Ticker ('/'-prefixed = futures, e.g. '/ES').
        interval: '5m' | '15m' | '1h' | '1d' (degree-bearing timeframe).
        backend: 'store' (latest nightly row), 'compute' (live recompute), or
            'auto' (store, falling back to compute).
    """
    with tool_call("get_wave_state", symbol=symbol, interval=interval):
        return _reader().get_state(symbol, interval, backend=backend)  # type: ignore[arg-type]


@mcp.tool()
def evaluate_wave_targets(symbol: str, interval: str = "1d") -> dict:
    """Numeric trade levels for a symbol's primary wave count.

    USE WHEN: an agent wants to reason about a concrete trade — entry context,
    stop, and Fibonacci targets — derived from the current count. Returns the
    primary count's invalidation (stop), its Fib targets, the current wave, and
    confidence. Empty `targets` means the count has no forward projection
    (e.g. a completed structure or no clear count).

    Args:
        symbol: Ticker ('/'-prefixed = futures).
        interval: '5m' | '15m' | '1h' | '1d'.
    """
    with tool_call("evaluate_wave_targets", symbol=symbol, interval=interval):
        state = _reader().get_state(symbol, interval, backend="auto")
        p = state.primary
        return {
            "symbol": state.symbol,
            "interval": state.interval,
            "has_count": p is not None,
            "current_wave": p.current_wave if p else None,
            "direction": p.direction if p else None,
            "confidence": p.confidence if p else 0.0,
            "probability": p.probability if p else 0.0,
            "entry": state.as_of_price,
            "stop_invalidation": p.invalidation if p else None,
            "targets": p.targets if p else {},
            "uncertainty": state.uncertainty,
            "engine_ver": state.engine_ver,
        }


@mcp.tool()
def list_wave_alerts(interval: str = "1d", min_probability: float = 0.6,
                     min_risk_reward: float = 2.0) -> list[WaveAlert]:
    """High-probability Elliott Wave trade setups across the tracked universe.

    USE WHEN: an agent wants the current actionable wave setups — "what wave
    trades look good today?". Each WaveAlert is a complete plan: entry, stop
    (= count invalidation), Fib target(s), risk:reward, and a day/swing tag.
    Only wave-3 / wave-5 impulse entries passing the gates are returned.

    Args:
        interval: '5m' | '15m' | '1h' | '1d'.
        min_probability: minimum primary-count probability (default 0.6).
        min_risk_reward: minimum reward:risk (default 2.0).
    """
    with tool_call("list_wave_alerts", interval=interval):
        return scan_alerts(interval, min_probability=min_probability,
                           min_risk_reward=min_risk_reward, reader=_reader())


@mcp.tool()
def scan_intraday_wave_alerts(
    symbols: str,
    interval: str = "5m",
    min_probability: float = 0.6,
    min_risk_reward: float = 2.0,
) -> list[WaveAlert]:
    """EW-7: On-demand intraday Elliott Wave alert scan from live ClickHouse bars.

    USE WHEN: an agent wants fresh intraday wave setups — "are there any 5-minute
    wave-3 entries forming on AAPL or TSLA right now?". Unlike list_wave_alerts
    (which reads the nightly-updated store), this recomputes counts live from
    ClickHouse so the answer reflects the current bar.

    Returns alerts where the primary count is in an impulse wave 3 or 5,
    probability >= min_probability, and R:R >= min_risk_reward.

    Args:
        symbols: Comma-separated tickers, e.g. "AAPL,TSLA,/GC".
        interval: '1m' | '5m' | '15m' | '30m' | '1h'.
        min_probability: minimum primary-count probability (default 0.6).
        min_risk_reward: minimum reward:risk (default 2.0).
    """
    with tool_call("scan_intraday_wave_alerts", symbols=symbols, interval=interval):
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        return scan_intraday_alerts(sym_list, interval,
                                    min_probability=min_probability,
                                    min_risk_reward=min_risk_reward)
