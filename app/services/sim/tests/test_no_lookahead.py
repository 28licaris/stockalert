"""
No-look-ahead / anti-cheating audit (portfolio pipeline).

Gold-standard causality test: run the FULL pipeline (signal source + filters +
dynamic-universe momentum ranking + portfolio fills) on a price series, then on the
SAME series truncated of its last K bars. Every trade the truncated run produces
must be an EXACT PREFIX of the full run's trades — appending future bars can never
change a past decision or fill. If any component used future data, earlier trades
would differ and this fails.

Sources covered here: breakout (+ dynamic top-N ranking) and ma_cross — both fire
reliably on synthetic data, exercising the source→ranking→fill path. The
pivot-based sources (divergence, elliott_wave) confirm pivots k bars later and have
their own dedicated no-look-ahead unit tests (app/indicators/tests/test_pivots_unit
+ app/signals/elliott/tests/test_elliott_no_lookahead).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from app.services.sim.backtester import Backtester
from app.services.sim.schemas import BacktestConfig

UTC = dt.timezone.utc
T0 = dt.datetime(2024, 1, 1, tzinfo=UTC)


@dataclass
class _Bar:
    symbol: str
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 2_000_000.0


def _series(symbol: str, n: int, slope: float) -> list[_Bar]:
    """Deterministic trend + oscillation → real highs/lows and MA crossovers."""
    bars, prev = [], 100.0
    for i in range(n):
        c = 100.0 + slope * i + 8.0 * math.sin(i / 5.0)
        bars.append(_Bar(symbol, T0 + dt.timedelta(days=i), open=prev,
                         high=max(prev, c) + 1.0, low=min(prev, c) - 1.0, close=c))
        prev = c
    return bars


def _cfg(symbols, top_n=None):
    return BacktestConfig(
        symbols=symbols, start=T0, end=T0 + dt.timedelta(days=500), interval="1d",
        starting_cash=100_000.0, history_window=400, max_concurrent_positions=5,
        max_portfolio_heat=0.5, momentum_top_n=top_n, momentum_lookback=20,
    )


def _run(monkeypatch, bars_by_symbol, strategy_params, top_n=None) -> list[tuple]:
    from app.services.sim.loader import build_strategy
    bt = Backtester()
    monkeypatch.setattr(bt, "_capture_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(bt, "_load_benchmark", lambda *a, **k: None)
    monkeypatch.setattr(bt, "_fetch_bars_multi", lambda *a, **k: {"1d": bars_by_symbol})
    strat = build_strategy("alert_driven", strategy_params, interval="1d")
    res = bt.run_portfolio(strat, _cfg(list(bars_by_symbol), top_n=top_n))
    return [(t.symbol, t.side, t.timestamp.isoformat(), round(t.price, 4),
             round(t.quantity, 4), t.is_closing) for t in res.trades]


_BREAKOUT = {"source": "breakout",
             "source_params": {"lookback": 10, "vol_mult": 1.0, "reward_risk_mult": 3.0},
             "filters": [], "filter_mode": "all", "risk_pct": 0.02, "max_risk_pct": 0.05,
             "min_reward_risk": 0.0}
_MACROSS = {"source": "ma_cross",
            "source_params": {"fast_period": 5, "slow_period": 15, "reward_risk_mult": 3.0},
            "filters": [], "filter_mode": "all", "risk_pct": 0.02, "max_risk_pct": 0.05,
            "min_reward_risk": 0.0}


def _assert_prefix(trunc, full):
    assert len(trunc) >= 1, "test is vacuous — no trades produced"
    assert trunc == full[: len(trunc)], (
        "LOOK-AHEAD: truncated-run trades are NOT a prefix of the full run — "
        "appending future bars changed a past trade.\n"
        f"trunc[-2:]={trunc[-2:]}\nfull_prefix[-2:]={full[: len(trunc)][-2:]}"
    )


def test_breakout_dynamic_pipeline_is_causal(monkeypatch):
    # Full pipeline incl. the dynamic top-N momentum ranking.
    full = {"A": _series("A", 160, 0.6), "B": _series("B", 160, 0.3)}
    trunc = {s: b[:130] for s, b in full.items()}
    _assert_prefix(_run(monkeypatch, trunc, _BREAKOUT, top_n=1),
                   _run(monkeypatch, full, _BREAKOUT, top_n=1))


def test_ma_cross_pipeline_is_causal(monkeypatch):
    full = {"A": _series("A", 160, 0.4)}
    trunc = {s: b[:130] for s, b in full.items()}
    _assert_prefix(_run(monkeypatch, trunc, _MACROSS),
                   _run(monkeypatch, full, _MACROSS))


def test_last_bar_decision_cannot_fill(monkeypatch):
    # A decision on the final bar has no next bar to fill at → it must NOT trade
    # (you cannot execute on a bar that hasn't opened). Dropping the last bar removes
    # at most the trades that needed it — never adds/changes earlier ones.
    full = {"A": _series("A", 140, 0.6)}
    a = _run(monkeypatch, full, _BREAKOUT)
    b = _run(monkeypatch, {"A": full["A"][:-1]}, _BREAKOUT)
    assert b == a[: len(b)]
