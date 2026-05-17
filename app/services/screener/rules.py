"""
Rule evaluators — one function per `RuleKind` in `schemas.py`.

Each evaluator takes a pre-computed OHLCV `pandas.DataFrame` for one
symbol plus the rule's `params` dict, and returns a `RuleEval` —
the boolean pass result plus the metric value that was compared
(so the response can echo "why did this pass?" back to the agent).

This module is the entire "what rules exist" surface. Adding a new
rule = new entry in `_RULE_EVALUATORS` + the matching enum value
in `schemas.RuleKind`. Tests in `test_screener.py` exercise each.

Indicators are computed via the existing `INDICATOR_REGISTRY` so the
screener's notion of "SMA(20)" matches everywhere else in the
platform (dashboard, MCP tools, backtester Context).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from app.indicators.registry import get_indicator
from app.indicators.bollinger import BollingerBands
from app.services.screener.schemas import ScreenerRule

logger = logging.getLogger(__name__)


@dataclass
class RuleEval:
    """Result of evaluating one rule against one symbol's bars."""

    passed: bool
    metric_name: str
    metric_value: float | None


# ─────────────────────────────────────────────────────────────────────
# Param accessors (raise clear errors on missing/bad input)
# ─────────────────────────────────────────────────────────────────────


def _require_int(params: dict, key: str, kind: str) -> int:
    if key not in params:
        raise ValueError(f"rule kind={kind!r} missing required int param {key!r}")
    try:
        return int(params[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"rule kind={kind!r} param {key!r} must be an int; got {params[key]!r}"
        ) from exc


def _require_float(params: dict, key: str, kind: str, default: float | None = None) -> float:
    if key not in params:
        if default is not None:
            return default
        raise ValueError(f"rule kind={kind!r} missing required float param {key!r}")
    try:
        return float(params[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"rule kind={kind!r} param {key!r} must be a float; got {params[key]!r}"
        ) from exc


def _latest(series: pd.Series) -> float | None:
    """Return the latest non-NaN value of a series, or None."""
    if len(series) == 0:
        return None
    val = series.iloc[-1]
    if val is None or (isinstance(val, float) and val != val):
        return None
    return float(val)


# ─────────────────────────────────────────────────────────────────────
# Rule evaluators
# ─────────────────────────────────────────────────────────────────────


def _close_above_sma(df: pd.DataFrame, params: dict) -> RuleEval:
    period = _require_int(params, "period", "close_above_sma")
    sma = get_indicator("sma", period=period).compute(df["close"])
    val = _latest(sma)
    close = _latest(df["close"])
    passed = val is not None and close is not None and close > val
    return RuleEval(passed=passed, metric_name=f"sma_{period}", metric_value=val)


def _close_below_sma(df: pd.DataFrame, params: dict) -> RuleEval:
    period = _require_int(params, "period", "close_below_sma")
    sma = get_indicator("sma", period=period).compute(df["close"])
    val = _latest(sma)
    close = _latest(df["close"])
    passed = val is not None and close is not None and close < val
    return RuleEval(passed=passed, metric_name=f"sma_{period}", metric_value=val)


def _close_above_ema(df: pd.DataFrame, params: dict) -> RuleEval:
    period = _require_int(params, "period", "close_above_ema")
    ema = get_indicator("ema", period=period).compute(df["close"])
    val = _latest(ema)
    close = _latest(df["close"])
    passed = val is not None and close is not None and close > val
    return RuleEval(passed=passed, metric_name=f"ema_{period}", metric_value=val)


def _close_below_ema(df: pd.DataFrame, params: dict) -> RuleEval:
    period = _require_int(params, "period", "close_below_ema")
    ema = get_indicator("ema", period=period).compute(df["close"])
    val = _latest(ema)
    close = _latest(df["close"])
    passed = val is not None and close is not None and close < val
    return RuleEval(passed=passed, metric_name=f"ema_{period}", metric_value=val)


def _rsi_above(df: pd.DataFrame, params: dict) -> RuleEval:
    period = _require_int(params, "period", "rsi_above")
    threshold = _require_float(params, "threshold", "rsi_above")
    rsi = get_indicator("rsi", period=period).compute(df["close"])
    val = _latest(rsi)
    passed = val is not None and val > threshold
    return RuleEval(passed=passed, metric_name=f"rsi_{period}", metric_value=val)


def _rsi_below(df: pd.DataFrame, params: dict) -> RuleEval:
    period = _require_int(params, "period", "rsi_below")
    threshold = _require_float(params, "threshold", "rsi_below")
    rsi = get_indicator("rsi", period=period).compute(df["close"])
    val = _latest(rsi)
    passed = val is not None and val < threshold
    return RuleEval(passed=passed, metric_name=f"rsi_{period}", metric_value=val)


def _close_at_lower_band(df: pd.DataFrame, params: dict) -> RuleEval:
    period = _require_int(params, "period", "close_at_lower_band")
    std_mult = _require_float(params, "std_multiplier", "close_at_lower_band", default=2.0)
    full = BollingerBands(period=period, std_multiplier=std_mult).compute_full(
        df["close"], df.get("high"), df.get("low"),
    )
    lower = _latest(full["lower"])
    close = _latest(df["close"])
    passed = lower is not None and close is not None and close <= lower
    return RuleEval(passed=passed, metric_name=f"bb_lower_{period}_{std_mult}", metric_value=lower)


def _close_at_upper_band(df: pd.DataFrame, params: dict) -> RuleEval:
    period = _require_int(params, "period", "close_at_upper_band")
    std_mult = _require_float(params, "std_multiplier", "close_at_upper_band", default=2.0)
    full = BollingerBands(period=period, std_multiplier=std_mult).compute_full(
        df["close"], df.get("high"), df.get("low"),
    )
    upper = _latest(full["upper"])
    close = _latest(df["close"])
    passed = upper is not None and close is not None and close >= upper
    return RuleEval(passed=passed, metric_name=f"bb_upper_{period}_{std_mult}", metric_value=upper)


def _atr_pct_above(df: pd.DataFrame, params: dict) -> RuleEval:
    return _atr_pct_compare(df, params, kind="atr_pct_above", above=True)


def _atr_pct_below(df: pd.DataFrame, params: dict) -> RuleEval:
    return _atr_pct_compare(df, params, kind="atr_pct_below", above=False)


def _atr_pct_compare(df: pd.DataFrame, params: dict, *, kind: str, above: bool) -> RuleEval:
    period = _require_int(params, "period", kind)
    threshold = _require_float(params, "threshold", kind)
    if "high" not in df.columns or "low" not in df.columns:
        return RuleEval(passed=False, metric_name=f"atr_pct_{period}", metric_value=None)
    atr = get_indicator("atr", period=period).compute(df["close"], df["high"], df["low"])
    atr_val = _latest(atr)
    close = _latest(df["close"])
    if atr_val is None or close is None or close <= 0:
        return RuleEval(passed=False, metric_name=f"atr_pct_{period}", metric_value=None)
    pct = atr_val / close
    passed = pct > threshold if above else pct < threshold
    return RuleEval(passed=passed, metric_name=f"atr_pct_{period}", metric_value=pct)


def _price_above(df: pd.DataFrame, params: dict) -> RuleEval:
    value = _require_float(params, "value", "price_above")
    close = _latest(df["close"])
    passed = close is not None and close > value
    return RuleEval(passed=passed, metric_name="close", metric_value=close)


def _price_below(df: pd.DataFrame, params: dict) -> RuleEval:
    value = _require_float(params, "value", "price_below")
    close = _latest(df["close"])
    passed = close is not None and close < value
    return RuleEval(passed=passed, metric_name="close", metric_value=close)


def _volume_above(df: pd.DataFrame, params: dict) -> RuleEval:
    value = _require_float(params, "value", "volume_above")
    if "volume" not in df.columns or len(df) == 0:
        return RuleEval(passed=False, metric_name="volume", metric_value=None)
    vol = _latest(df["volume"])
    passed = vol is not None and vol > value
    return RuleEval(passed=passed, metric_name="volume", metric_value=vol)


# ─────────────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────────────

_RULE_EVALUATORS: dict[str, Callable[[pd.DataFrame, dict[str, Any]], RuleEval]] = {
    "close_above_sma": _close_above_sma,
    "close_below_sma": _close_below_sma,
    "close_above_ema": _close_above_ema,
    "close_below_ema": _close_below_ema,
    "rsi_above": _rsi_above,
    "rsi_below": _rsi_below,
    "close_at_lower_band": _close_at_lower_band,
    "close_at_upper_band": _close_at_upper_band,
    "atr_pct_above": _atr_pct_above,
    "atr_pct_below": _atr_pct_below,
    "price_above": _price_above,
    "price_below": _price_below,
    "volume_above": _volume_above,
}


def evaluate(rule: ScreenerRule, df: pd.DataFrame) -> RuleEval:
    """
    Evaluate one rule against a symbol's bar DataFrame.

    Raises `ValueError` for unknown rule kinds or missing params —
    these are spec authoring errors, not runtime conditions, so we
    surface them loudly rather than degrade. Per-symbol runtime
    errors (missing data, NaN indicators, etc.) return
    `RuleEval(passed=False, ...)` so the scan continues.
    """
    evaluator = _RULE_EVALUATORS.get(rule.kind)
    if evaluator is None:
        supported = ", ".join(sorted(_RULE_EVALUATORS))
        raise ValueError(
            f"Unknown rule kind {rule.kind!r}. Supported: {supported}."
        )
    return evaluator(df, rule.params)
