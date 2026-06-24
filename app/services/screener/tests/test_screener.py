"""
Unit tests for the screener service (`app/services/screener/`).

Coverage:
  1. Each rule kind in `rules.py` — passes/fails on a hand-crafted
     OHLCV DataFrame with known outcomes.
  2. Bad rule kinds / missing params raise `ValueError` with a clear
     message at evaluate time (spec author errors, not runtime).
  3. `Screener.scan` orchestration — universe resolution (explicit +
     watchlist union), per-symbol fetch failures land in `errors[]`
     without breaking the scan, ranking + `limit` truncation, etc.
  4. The `ScreenerSpec` model rejects empty universe sources.

Strategy: tests inject stub readers + stub watchlist services into
`Screener(...)`, so no ClickHouse / Iceberg / live infra is hit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

from app.services.readers.schemas import LiveBar
from app.services.screener.rules import RuleEval, evaluate
from app.services.screener.schemas import (
    ScreenerRule,
    ScreenerSpec,
)
from app.services.screener.screener import Screener


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _df(closes, *, highs=None, lows=None, volumes=None) -> pd.DataFrame:
    """Build an OHLCV DataFrame indexed by integer position."""
    n = len(closes)
    if highs is None:
        highs = [c + 1.0 for c in closes]
    if lows is None:
        lows = [c - 1.0 for c in closes]
    if volumes is None:
        volumes = [1_000_000.0] * n
    return pd.DataFrame({
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


def _live_bar(symbol: str, ts: datetime, close: float, volume: float = 1e6) -> LiveBar:
    return LiveBar(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
        interval="1d",
    )


class _StubBarReader:
    """Returns canned LiveBar lists per symbol; raises on unknown."""

    def __init__(self, data: dict[str, list[LiveBar]]) -> None:
        self._data = data

    def get_bars_in_range(
        self, symbol, start, end, *, interval="1m", limit=10_000,
        source_table=None,
    ) -> list[LiveBar]:
        if symbol not in self._data:
            return []
        return self._data[symbol]


class _StubExplodingBarReader:
    """Raises on every call — exercises the per-symbol error path."""

    def get_bars_in_range(self, *args, **kwargs):
        raise RuntimeError("upstream CH unreachable")


class _StubWatchlistService:
    def __init__(self, members: dict[str, list[str]]) -> None:
        self._members = members

    def list_members(self, name: str) -> list[str]:
        return list(self._members.get(name, []))


# ─────────────────────────────────────────────────────────────────────
# Rule evaluators — each `RuleKind`
# ─────────────────────────────────────────────────────────────────────


def test_rule_close_above_sma_passes_on_uptrend() -> None:
    df = _df([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
    res = evaluate(ScreenerRule(kind="close_above_sma", params={"period": 3}), df)
    # SMA(3) on last 3 = (18+19+20)/3 = 19; close=20 > 19.
    assert res.passed is True
    assert res.metric_name == "sma_3"
    assert res.metric_value == pytest.approx(19.0)


def test_rule_close_below_sma_passes_on_downtrend() -> None:
    df = _df([20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10])
    res = evaluate(ScreenerRule(kind="close_below_sma", params={"period": 3}), df)
    # SMA(3) on last 3 = (12+11+10)/3 = 11; close=10 < 11.
    assert res.passed is True


def test_rule_close_above_ema_passes_on_uptrend() -> None:
    df = _df([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
    res = evaluate(ScreenerRule(kind="close_above_ema", params={"period": 5}), df)
    assert res.passed is True
    assert res.metric_name == "ema_5"


def test_rule_close_below_ema_fails_when_above() -> None:
    df = _df([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
    res = evaluate(ScreenerRule(kind="close_below_ema", params={"period": 5}), df)
    assert res.passed is False


def test_rule_rsi_above_with_uptrend() -> None:
    # Mostly-up with small pullbacks (the RSI impl fills no-loss windows
    # to 50, so we need actual down ticks). Big gains + tiny losses → RSI > 70.
    closes = [10.0, 12, 11.9, 14, 13.9, 16, 15.9, 18, 17.9, 20,
              19.9, 22, 21.9, 24, 23.9, 26, 25.9, 28, 27.9, 30,
              29.9, 32, 31.9, 34, 33.9, 36]
    df = _df(closes)
    res = evaluate(
        ScreenerRule(kind="rsi_above", params={"period": 14, "threshold": 70.0}),
        df,
    )
    assert res.passed is True
    assert res.metric_value is not None and res.metric_value > 70.0


def test_rule_rsi_below_with_downtrend() -> None:
    closes = [36.0, 34, 34.1, 32, 32.1, 30, 30.1, 28, 28.1, 26,
              26.1, 24, 24.1, 22, 22.1, 20, 20.1, 18, 18.1, 16,
              16.1, 14, 14.1, 12, 12.1, 10]
    df = _df(closes)
    res = evaluate(
        ScreenerRule(kind="rsi_below", params={"period": 14, "threshold": 30.0}),
        df,
    )
    assert res.passed is True
    assert res.metric_value is not None and res.metric_value < 30.0


def test_rule_bollinger_lower_band_touch() -> None:
    # Long flat series then a hard drop on the last bar so close < mean - 2*std.
    closes = [100.0] * 25 + [50.0]
    df = _df(closes)
    res = evaluate(
        ScreenerRule(
            kind="close_at_lower_band",
            params={"period": 20, "std_multiplier": 2.0},
        ),
        df,
    )
    assert res.passed is True
    assert res.metric_name == "bb_lower_20_2.0"


def test_rule_bollinger_upper_band_touch() -> None:
    closes = [100.0] * 25 + [150.0]
    df = _df(closes)
    res = evaluate(
        ScreenerRule(
            kind="close_at_upper_band",
            params={"period": 20, "std_multiplier": 2.0},
        ),
        df,
    )
    assert res.passed is True


def test_rule_atr_pct_above_on_volatile_series() -> None:
    # Alternating big swings produce a high ATR/close ratio.
    closes = [100.0, 110.0, 90.0, 115.0, 85.0, 120.0, 80.0, 125.0,
              75.0, 130.0, 70.0, 135.0, 65.0, 140.0, 60.0, 145.0]
    df = _df(
        closes,
        highs=[c + 10 for c in closes],
        lows=[c - 10 for c in closes],
    )
    res = evaluate(
        ScreenerRule(kind="atr_pct_above", params={"period": 14, "threshold": 0.05}),
        df,
    )
    assert res.passed is True
    assert res.metric_value is not None and res.metric_value > 0.05


def test_rule_atr_pct_below_on_calm_series() -> None:
    # Flat series → ATR ≈ 0 → ATR/close very small.
    closes = [100.0] * 20
    df = _df(
        closes,
        highs=[100.01 for _ in closes],
        lows=[99.99 for _ in closes],
    )
    res = evaluate(
        ScreenerRule(kind="atr_pct_below", params={"period": 14, "threshold": 0.001}),
        df,
    )
    assert res.passed is True


def test_rule_price_above_and_below() -> None:
    df = _df([10, 20, 30, 40, 50])
    assert evaluate(
        ScreenerRule(kind="price_above", params={"value": 49.0}), df,
    ).passed is True
    assert evaluate(
        ScreenerRule(kind="price_above", params={"value": 51.0}), df,
    ).passed is False
    assert evaluate(
        ScreenerRule(kind="price_below", params={"value": 51.0}), df,
    ).passed is True


def test_rule_volume_above() -> None:
    df = _df([10, 11, 12], volumes=[1e5, 2e5, 5e6])
    assert evaluate(
        ScreenerRule(kind="volume_above", params={"value": 1e6}), df,
    ).passed is True
    assert evaluate(
        ScreenerRule(kind="volume_above", params={"value": 1e7}), df,
    ).passed is False


# ─────────────────────────────────────────────────────────────────────
# Spec-author errors raise at evaluate time
# ─────────────────────────────────────────────────────────────────────


def test_evaluate_raises_on_missing_param() -> None:
    df = _df([10, 11, 12, 13])
    with pytest.raises(ValueError, match="missing required int param 'period'"):
        evaluate(ScreenerRule(kind="close_above_sma", params={}), df)


def test_evaluate_raises_on_bad_param_type() -> None:
    df = _df([10, 11, 12, 13])
    with pytest.raises(ValueError, match="must be an int"):
        evaluate(ScreenerRule(kind="close_above_sma", params={"period": "abc"}), df)


# ─────────────────────────────────────────────────────────────────────
# ScreenerSpec validation
# ─────────────────────────────────────────────────────────────────────


def test_spec_requires_universe_or_watchlist() -> None:
    with pytest.raises(ValidationError, match="universe.*watchlist_name"):
        ScreenerSpec(rules=[ScreenerRule(kind="price_above", params={"value": 1.0})])


def test_spec_accepts_universe_only() -> None:
    spec = ScreenerSpec(
        universe=["aapl", "msft"],
        rules=[ScreenerRule(kind="price_above", params={"value": 1.0})],
    )
    assert spec.universe == ["aapl", "msft"]


def test_spec_rejects_empty_rules() -> None:
    with pytest.raises(ValidationError):
        ScreenerSpec(universe=["AAPL"], rules=[])


# ─────────────────────────────────────────────────────────────────────
# Screener.scan — orchestration
# ─────────────────────────────────────────────────────────────────────


def _bars_uptrend(symbol: str, start: float = 10.0, n: int = 30) -> list[LiveBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        _live_bar(symbol, base + timedelta(days=i), start + i, volume=1e6 + i * 1e4)
        for i in range(n)
    ]


def _bars_flat(symbol: str, price: float = 50.0, n: int = 30) -> list[LiveBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        _live_bar(symbol, base + timedelta(days=i), price, volume=2e6)
        for i in range(n)
    ]


def test_screener_scan_filters_universe_by_rules() -> None:
    """AAPL is uptrending (close > SMA), TEST is flat (close == SMA)."""
    reader = _StubBarReader({
        "AAPL": _bars_uptrend("AAPL"),
        "TEST": _bars_flat("TEST"),
    })
    screener = Screener(bar_reader=reader)
    spec = ScreenerSpec(
        universe=["AAPL", "TEST"],
        interval="1d",
        lookback_bars=30,
        rules=[ScreenerRule(kind="close_above_sma", params={"period": 10})],
    )
    result = screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))

    assert result.universe_size == 2
    assert result.n_passed == 1
    assert [c.symbol for c in result.candidates] == ["AAPL"]
    assert result.rejected_count == 1
    assert result.errors == []


def test_screener_scan_ranks_by_volume_descending() -> None:
    """Both pass; higher-volume symbol comes first."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _bars(sym: str, vol_last: float) -> list[LiveBar]:
        bars = _bars_uptrend(sym)
        # Override the last bar's volume so the rank is deterministic.
        bars[-1] = _live_bar(sym, base + timedelta(days=29), 39.0, volume=vol_last)
        return bars

    reader = _StubBarReader({
        "LOW_VOL": _bars("LOW_VOL", 1e5),
        "HIGH_VOL": _bars("HIGH_VOL", 1e8),
    })
    screener = Screener(bar_reader=reader)
    spec = ScreenerSpec(
        universe=["LOW_VOL", "HIGH_VOL"],
        interval="1d",
        lookback_bars=30,
        rules=[ScreenerRule(kind="close_above_sma", params={"period": 10})],
        rank_by="volume",
    )
    result = screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert [c.symbol for c in result.candidates] == ["HIGH_VOL", "LOW_VOL"]
    assert result.candidates[0].score > result.candidates[1].score


def test_screener_scan_per_symbol_fetch_error_lands_in_errors_list() -> None:
    """One symbol's reader explodes; the scan completes for the rest."""

    class _PartialReader:
        def __init__(self, good_data, bad_symbol):
            self._good = good_data
            self._bad = bad_symbol

        def get_bars_in_range(self, symbol, start, end, *, interval="1m",
                              limit=10_000, source_table=None):
            if symbol == self._bad:
                raise RuntimeError("boom")
            return self._good.get(symbol, [])

    reader = _PartialReader({"AAPL": _bars_uptrend("AAPL")}, bad_symbol="BROKEN")
    screener = Screener(bar_reader=reader)
    spec = ScreenerSpec(
        universe=["AAPL", "BROKEN"],
        interval="1d",
        lookback_bars=30,
        rules=[ScreenerRule(kind="close_above_sma", params={"period": 10})],
    )
    result = screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert result.n_passed == 1
    assert [c.symbol for c in result.candidates] == ["AAPL"]
    assert len(result.errors) == 1
    assert result.errors[0]["symbol"] == "BROKEN"
    assert "boom" in result.errors[0]["error"]
    # rejected_count includes the errored symbol.
    assert result.rejected_count == 1


def test_screener_scan_empty_bars_count_as_rejected_not_error() -> None:
    """Symbols with no bars are silently rejected (no row in `errors`)."""
    reader = _StubBarReader({})  # every symbol returns []
    screener = Screener(bar_reader=reader)
    spec = ScreenerSpec(
        universe=["AAA", "BBB"],
        interval="1d",
        lookback_bars=30,
        rules=[ScreenerRule(kind="close_above_sma", params={"period": 5})],
    )
    result = screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert result.n_passed == 0
    assert result.errors == []
    assert result.rejected_count == 2


def test_screener_scan_limit_truncates_after_rank() -> None:
    """With limit=2, only top-2 by score are returned."""
    reader = _StubBarReader({
        f"S{i}": _bars_uptrend(f"S{i}", start=10.0 + i) for i in range(5)
    })
    screener = Screener(bar_reader=reader)
    spec = ScreenerSpec(
        universe=[f"S{i}" for i in range(5)],
        interval="1d",
        lookback_bars=30,
        rules=[ScreenerRule(kind="close_above_sma", params={"period": 10})],
        rank_by="none",
        limit=2,
    )
    result = screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert result.n_passed == 2
    assert len(result.candidates) == 2


def test_screener_scan_universe_dedups_and_uppercases() -> None:
    """Mixed-case duplicates collapse to a single symbol."""
    reader = _StubBarReader({"AAPL": _bars_uptrend("AAPL")})
    screener = Screener(bar_reader=reader)
    spec = ScreenerSpec(
        universe=["aapl", "AAPL", "  AAPL  "],
        interval="1d",
        lookback_bars=30,
        rules=[ScreenerRule(kind="close_above_sma", params={"period": 10})],
    )
    result = screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert result.universe_size == 1
    assert result.n_passed == 1


def test_screener_scan_merges_watchlist_universe() -> None:
    """`universe` + `watchlist_name` members are unioned (deduped)."""
    reader = _StubBarReader({
        "AAPL": _bars_uptrend("AAPL"),
        "MSFT": _bars_uptrend("MSFT"),
        "GOOG": _bars_uptrend("GOOG"),
    })
    wl = _StubWatchlistService({"core": ["MSFT", "GOOG"]})
    screener = Screener(bar_reader=reader, watchlist_service=wl)
    spec = ScreenerSpec(
        universe=["AAPL"],
        watchlist_name="core",
        interval="1d",
        lookback_bars=30,
        rules=[ScreenerRule(kind="close_above_sma", params={"period": 10})],
        rank_by="none",
    )
    result = screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert result.universe_size == 3
    assert {c.symbol for c in result.candidates} == {"AAPL", "MSFT", "GOOG"}


def test_screener_scan_bad_rule_kind_raises() -> None:
    """Unknown rule kind raises ValueError at scan time (author error)."""
    reader = _StubBarReader({"AAPL": _bars_uptrend("AAPL")})
    screener = Screener(bar_reader=reader)
    # Bypass Pydantic enforcement by constructing a model_validate dict
    # with a kind that doesn't match the Literal — Pydantic would normally
    # reject, but we want to assert the runtime guard too.
    bad_rule = ScreenerRule.model_construct(kind="nonexistent_rule", params={})
    spec = ScreenerSpec.model_construct(
        universe=["AAPL"],
        watchlist_name=None,
        interval="1d",
        provider="polygon",
        lookback_bars=30,
        rules=[bad_rule],
        rank_by="none",
        limit=20,
    )
    with pytest.raises(ValueError, match="Unknown rule kind"):
        screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))


def test_screener_scan_all_rules_must_pass_logical_and() -> None:
    """A symbol must satisfy every rule (logical AND) to pass."""
    reader = _StubBarReader({"AAPL": _bars_uptrend("AAPL")})
    screener = Screener(bar_reader=reader)
    spec = ScreenerSpec(
        universe=["AAPL"],
        interval="1d",
        lookback_bars=30,
        rules=[
            ScreenerRule(kind="close_above_sma", params={"period": 10}),
            # Bar 30 will be 39 → fails this.
            ScreenerRule(kind="price_above", params={"value": 1_000.0}),
        ],
    )
    result = screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert result.n_passed == 0


def test_screener_scan_no_snapshot_id_on_ch_intervals() -> None:
    """`snapshot_id` is None for non-1m intervals (CH live tier)."""
    reader = _StubBarReader({"AAPL": _bars_uptrend("AAPL")})
    screener = Screener(bar_reader=reader)
    spec = ScreenerSpec(
        universe=["AAPL"],
        interval="1d",
        lookback_bars=30,
        rules=[ScreenerRule(kind="close_above_sma", params={"period": 10})],
    )
    result = screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert result.snapshot_id is None


def test_screener_scan_metrics_echo_rule_outputs() -> None:
    """Each Candidate echoes one metric per rule (transparency for agents)."""
    reader = _StubBarReader({"AAPL": _bars_uptrend("AAPL")})
    screener = Screener(bar_reader=reader)
    spec = ScreenerSpec(
        universe=["AAPL"],
        interval="1d",
        lookback_bars=30,
        rules=[
            ScreenerRule(kind="close_above_sma", params={"period": 10}),
            ScreenerRule(kind="price_above", params={"value": 1.0}),
        ],
    )
    result = screener.scan(spec, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert result.n_passed == 1
    metrics = result.candidates[0].metrics
    assert len(metrics) == 2
    names = [m.name for m in metrics]
    assert names == ["sma_10", "close"]
