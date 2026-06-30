"""
IndicatorReader — single source of truth for indicator computation
across all consumers (dashboard, MCP agents, backtester via Context).

Reads bars from `BronzeReader` (1m / historical / CH-independent) or
`BarReader` (live tier, all other intervals), computes indicators via
the `INDICATOR_REGISTRY`, and returns Pydantic shapes. Same code path
across surfaces means the SMA(20) value at timestamp T is identical
no matter who's asking.

Full architectural design: `docs/indicator_exposure_design.md`. This
module is the implementation of §4.3 (the reader service).

Phase-6 Gold path: when pre-computed feature tables ship in the
data plan, this class gains a `backend` config knob ('compute' or
'gold'). Consumers don't see the swap — the contract is the
Pydantic shape, not the compute strategy.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd

from app.indicators.registry import get_indicator
from app.services.readers.schemas import (
    BronzeBar,
    IndicatorChartData,
    IndicatorSeries,
    IndicatorValue,
)
from app.services.sim.intervals import interval_seconds, supported_intervals

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Cross-timeframe (source-aggregation) support
# ─────────────────────────────────────────────────────────────────────
#
# A moving average's `length` counts BARS, not calendar time. "200-day
# SMA" therefore means {source_agg: 1d, length: 200} regardless of the
# chart's display interval. When source_agg is coarser than the display
# interval, we compute the MA on the coarser series and forward-fill
# ("step") it onto the display bars — matching how thinkorswim draws a
# daily MA on an intraday chart. Full rationale:
# `docs/standards/` + `app/indicators/ema.py` seed-continuity note.

# Recursive indicators reseed from the first bar they are handed, so a
# bare display window is wrong. We prepend warmup source bars; the seed
# error decays as (1-alpha)^n. ~6x length drives it below ~1e-4 of price
# — well under chart/alert resolution. Non-recursive (SMA/WMA) need only
# `length` trailing bars (slice-invariant; see test_ma_timeframe_contract).
_RECURSIVE_INDICATORS = {"ema", "rsi", "tsi", "macd"}
_EMA_WARMUP_FACTOR = 6

# Multi-output indicators are not yet supported across aggregations — the
# MA use case (single series) is the contract for Phase 1.
_CROSS_TF_UNSUPPORTED = {"bollinger", "stochastic", "macd"}


# Indicators with `compute_full` return one IndicatorSeries per
# component. The map declares the canonical component naming so the
# wire format is stable regardless of which consumer asks.
#
# For "bollinger": the indicator's compute_full returns a dict with
# keys upper/middle/lower/bandwidth/percent_b. We prefix with the
# indicator name to produce series names bollinger_upper,
# bollinger_middle, etc. Same pattern for stochastic.
#
# MACD's compute_full returns a tuple (line, signal, histogram).
# We map positionally; documented under `_expand_indicator`.
_MULTI_OUTPUT_INDICATORS = {"bollinger", "stochastic", "macd"}


class IndicatorReader:
    """
    Read interface over computed indicators. Stateless; cheap to
    construct per request. Underlying bar readers are
    `lru_cache`'d so reuse is implicit.
    """

    @classmethod
    def from_settings(cls) -> "IndicatorReader":
        return cls()

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def get_series(
        self,
        symbol: str,
        indicator: str,
        params: dict[str, Any],
        start: datetime,
        end: datetime,
        *,
        interval: str = "1d",
        provider: str = "polygon",
        label: Optional[str] = None,
        source_agg: Optional[str] = None,
        window_days: Optional[int] = None,
    ) -> IndicatorSeries:
        """
        Single-indicator query → single `IndicatorSeries`.

        For multi-output indicators (Bollinger / Stochastic / MACD)
        this returns only the canonical component (middle band /
        %K / MACD line — same as the indicator's `compute()` method).
        To get all components, use `get_chart_data` with one spec.

        Cross-timeframe: pass `source_agg` (bar-locked, e.g. '1d' for a
        200-day SMA on a 5m chart) or `window_days` (window-locked — pins
        source_agg='1d', period=window_days). Both resolve to the same
        coarser-series compute, forward-filled onto the display interval.
        """
        chart = self._compute_chart_data(
            symbol=symbol,
            indicator_specs=[{
                "name": indicator, "params": params, "label": label,
                "source_agg": source_agg, "window_days": window_days,
            }],
            start=start, end=end,
            interval=interval, provider=provider,
            include_bars=False,
            single_canonical_only=True,
            raise_on_indicator_error=True,
        )
        if not chart.series:
            return IndicatorSeries(
                name=indicator.lower(),
                params=params,
                label=label or _format_label(indicator, params),
                values=[],
                count=0,
            )
        return chart.series[0]

    def get_chart_data(
        self,
        symbol: str,
        indicator_specs: list[dict[str, Any]],
        start: datetime,
        end: datetime,
        *,
        interval: str = "1d",
        provider: str = "polygon",
    ) -> IndicatorChartData:
        """
        Multi-indicator query → bars + N indicator series in one
        response. Multi-output indicators expand into N component
        series (e.g. one `IndicatorSeries` for each of
        `bollinger_upper`, `bollinger_middle`, `bollinger_lower`,
        etc.).

        Each `indicator_specs` entry is a dict with:
          - `name`: indicator registry name (`sma`, `bollinger`, ...)
          - `params`: dict of indicator-constructor kwargs
          - `label`: optional display label (defaults computed)
        """
        return self._compute_chart_data(
            symbol=symbol,
            indicator_specs=indicator_specs,
            start=start, end=end,
            interval=interval, provider=provider,
            include_bars=True,
            single_canonical_only=False,
            raise_on_indicator_error=False,
        )

    # ─────────────────────────────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────────────────────────────

    def _compute_chart_data(
        self,
        *,
        symbol: str,
        indicator_specs: list[dict[str, Any]],
        start: datetime,
        end: datetime,
        interval: str,
        provider: str,
        include_bars: bool,
        single_canonical_only: bool,
        raise_on_indicator_error: bool,
    ) -> IndicatorChartData:
        # `provider` is retained on the public API for backward compat but
        # no longer selects a bar source — everything reads from CH now.
        _ = provider
        bars, snapshot_id = self._fetch_bars(symbol, start, end, interval)
        if not bars:
            return IndicatorChartData(
                symbol=symbol, interval=interval, start=start, end=end,
                bars=[], series=[], snapshot_id=snapshot_id,
            )

        df = _bars_to_df(bars)
        close, high, low = df["close"], df["high"], df["low"]

        series_out: list[IndicatorSeries] = []
        for spec in indicator_specs:
            name = str(spec["name"]).lower()
            label_override = spec.get("label")
            try:
                source_agg, spec_params = self._resolve_source_agg(spec, interval)
            except ValueError as exc:
                if raise_on_indicator_error:
                    raise
                logger.warning("IndicatorReader: bad source_agg for %s: %s", name, exc)
                series_out.append(IndicatorSeries(
                    name=name, params=dict(spec.get("params") or {}),
                    label=f"{label_override or name} (error: {exc})",
                    values=[], count=0,
                ))
                continue

            try:
                if source_agg is not None:
                    # Cross-timeframe: compute the MA on the coarser source
                    # series, then forward-fill ("step") onto the display bars.
                    components = {
                        name: self._compute_cross_tf(
                            symbol, name, spec_params, source_agg,
                            display_index=df.index, end=end,
                        )
                    }
                else:
                    components = self._expand_indicator(
                        name, spec_params, close, high, low,
                        canonical_only=single_canonical_only,
                    )
            except ValueError as exc:
                if raise_on_indicator_error:
                    # Single-indicator call (get_series) — programmer
                    # error or bad input. Surface the exception cleanly.
                    raise
                # Multi-indicator chart-data path — degrade so one bad
                # indicator doesn't drop the rest.
                logger.warning("IndicatorReader: %s%s failed: %s", name, spec_params, exc)
                series_out.append(IndicatorSeries(
                    name=name, params=spec_params,
                    label=f"{label_override or name} (error: {exc})",
                    values=[], count=0, source_agg=source_agg,
                ))
                continue
            for component_name, pd_series in components.items():
                values = _pd_series_to_indicator_values(df.index, pd_series)
                # Multi-output: label per-component. Single-output:
                # honor the caller's label override if present.
                if component_name == name and label_override:
                    label = label_override
                else:
                    label = _format_label(
                        name, spec_params,
                        component=component_name if component_name != name else None,
                        source_agg=source_agg,
                    )
                series_out.append(IndicatorSeries(
                    name=component_name, params=spec_params, label=label,
                    values=values, count=len(values), source_agg=source_agg,
                ))

        return IndicatorChartData(
            symbol=symbol,
            interval=interval,
            start=start,
            end=end,
            bars=bars if include_bars else [],
            series=series_out,
            snapshot_id=snapshot_id,
        )

    def _expand_indicator(
        self,
        name: str,
        params: dict[str, Any],
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        *,
        canonical_only: bool,
    ) -> dict[str, pd.Series]:
        """
        Compute the indicator and decompose multi-output indicators
        into named components.

        Returns a dict keyed by the OUTPUT series name (already
        prefixed for multi-output, e.g. 'bollinger_upper'). Each
        value is a pd.Series aligned to the bar window's index.

        When `canonical_only` is True, multi-output indicators
        return only the canonical single-output (same as their
        `compute()` method). Used by `get_series()` so the
        single-indicator route stays unambiguous.
        """
        name_lower = name.lower()

        if name_lower == "bollinger" and not canonical_only:
            from app.indicators.bollinger import BollingerBands
            ind = BollingerBands(**params)
            full = ind.compute_full(close, high, low)
            return {f"bollinger_{k}": v for k, v in full.items()}

        if name_lower == "stochastic" and not canonical_only:
            from app.indicators.stochastic import StochasticOscillator
            ind = StochasticOscillator(**params)
            full = ind.compute_full(close, high, low)
            return {f"stochastic_{k}": v for k, v in full.items()}

        if name_lower == "macd" and not canonical_only:
            from app.indicators.macd import MACD
            ind = MACD(**params)
            line, signal, histogram = ind.compute_full(close)
            return {
                "macd": line,
                "macd_signal": signal,
                "macd_histogram": histogram,
            }

        # Default: single-output indicator OR canonical-only call.
        # `get_indicator` raises ValueError on unknown names.
        ind = get_indicator(name, **params)
        # ATR and Stochastic require high/low; others ignore them.
        series = ind.compute(close, high, low)
        return {name_lower: series}

    # ─────────────────────────────────────────────────────────────────
    # Cross-timeframe (source-aggregation) resolution + compute
    # ─────────────────────────────────────────────────────────────────

    def _resolve_source_agg(
        self, spec: dict[str, Any], display_interval: str,
    ) -> tuple[Optional[str], dict[str, Any]]:
        """
        Resolve a spec's display-independent source aggregation.

        Both request styles collapse to the same `(source_agg, params)`:
          - window-locked: spec carries `window_days` (a calendar concept) →
            source_agg pinned to '1d', `period` = window_days.
          - bar-locked: spec carries optional `source_agg` → used directly.

        Returns `(source_agg, params)`. `source_agg` is None when the MA is
        computed on the display interval (no cross-TF work) — the common
        case, and also when source_agg resolves equal to the display
        interval. Raises ValueError on an unknown or finer-than-display
        source_agg (base bars roll up, never down).
        """
        params = dict(spec.get("params") or {})
        window_days = spec.get("window_days")
        raw_source = spec.get("source_agg")

        if window_days is not None:
            wd = int(window_days)
            if wd < 1:
                raise ValueError(f"window_days must be >= 1, got {window_days}")
            params["period"] = wd
            source_agg = "1d"
        elif raw_source:
            source_agg = str(raw_source).lower()
        else:
            return None, params

        supported = supported_intervals()
        if source_agg not in supported:
            raise ValueError(
                f"source_agg {source_agg!r} not supported. One of: {', '.join(supported)}."
            )
        src_s = interval_seconds(source_agg)
        disp_s = interval_seconds(display_interval)
        if src_s < disp_s:
            raise ValueError(
                f"source_agg {source_agg!r} ({src_s}s) is finer than display "
                f"interval {display_interval!r} ({disp_s}s); coarser-or-equal "
                "only (base bars roll up, never down)."
            )
        if src_s == disp_s:
            return None, params  # same TF → ordinary single-TF path
        return source_agg, params

    def _compute_cross_tf(
        self,
        symbol: str,
        name: str,
        params: dict[str, Any],
        source_agg: str,
        *,
        display_index: pd.Index,
        end: datetime,
    ) -> pd.Series:
        """
        Compute a single-series indicator over the coarser `source_agg`
        bars and forward-fill it onto the display bars.

        Warmup: fetch `warmup_bars` source bars BEFORE the display window so
        the MA is fully formed across it — and, for recursive (EMA-family)
        indicators, converged past the seed. The under-converged head is
        masked so a half-formed average can never render or fire an alert.
        """
        if name in _CROSS_TF_UNSUPPORTED:
            raise ValueError(
                f"{name!r} is multi-output; source_agg is supported for "
                "single-series indicators (the moving-average use case) only."
            )
        if display_index is None or len(display_index) == 0:
            return pd.Series(dtype=float)

        period = int(params.get("period", 20))
        warmup_bars = (
            period * _EMA_WARMUP_FACTOR if name in _RECURSIVE_INDICATORS else period
        )

        display_start = pd.Timestamp(display_index[0]).to_pydatetime()
        fetch_start = display_start - _warmup_lookback(source_agg, warmup_bars)

        src_bars, _ = self._fetch_bars(symbol, fetch_start, end, source_agg)
        if not src_bars:
            return pd.Series(index=pd.DatetimeIndex(display_index), dtype=float)

        src_df = _bars_to_df(src_bars)
        ind = get_indicator(name, **params)
        src_ma = ind.compute(
            src_df["close"], src_df.get("high"), src_df.get("low"),
        )

        # Recursive MAs: drop the under-converged head (SMA/WMA are already
        # NaN-masked by min_periods, so this is a no-op for them).
        if name in _RECURSIVE_INDICATORS and warmup_bars > 0:
            src_ma = src_ma.copy()
            src_ma.iloc[:warmup_bars] = np.nan

        return _forward_fill_onto(src_ma, display_index)

    # ─────────────────────────────────────────────────────────────────
    # Bar source selection — mirrors `Backtester._fetch_bars`
    # ─────────────────────────────────────────────────────────────────

    def _fetch_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> tuple[list[BronzeBar], Optional[str]]:
        """
        Read bars from ClickHouse — the hot cache is the single read
        source for EVERY interval, 1m included. This keeps indicators
        aligned with the candles the chart shows (which also come from
        CH via the bars gateway). A missing 1m window is healed
        out-of-band by the lake→CH sync (``ch_reconcile`` /
        ``reconcile_ch_from_schwab``), NOT by reading the lake here.

        `snapshot_id` is always None — CH has no Iceberg snapshots. The
        tuple shape is kept for `IndicatorChartData.snapshot_id`.
        """
        return self._fetch_bars_ch(symbol, start, end, interval)

    def _fetch_bars_ch(
        self, symbol: str, start: datetime, end: datetime, interval: str,
    ) -> tuple[list[BronzeBar], Optional[str]]:
        """Convert LiveBar → BronzeBar so the response shape stays uniform."""
        from app.services.readers.bar_reader import BarReader

        reader = BarReader.from_settings()
        live_bars = reader.get_bars_in_range(
            symbol, start, end, interval=interval, limit=100_000,
        )
        bronze_bars = [
            BronzeBar(
                symbol=lb.symbol,
                timestamp=lb.timestamp,
                open=lb.open, high=lb.high, low=lb.low, close=lb.close,
                volume=lb.volume,
                vwap=lb.vwap,
                trade_count=lb.trade_count,
                source=lb.source or "clickhouse",
            )
            for lb in live_bars
        ]
        return bronze_bars, None  # CH has no snapshot


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _bars_to_df(bars: list[BronzeBar]) -> pd.DataFrame:
    """OHLCV DataFrame indexed by timestamp. One row per bar."""
    rows = [
        {
            "timestamp": b.timestamp,
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": b.volume,
        }
        for b in bars
    ]
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.set_index("timestamp")
    return df


def _warmup_lookback(source_agg: str, warmup_bars: int) -> timedelta:
    """
    Calendar lookback that comfortably contains `warmup_bars` source bars
    before a window start. Markets are closed nights/weekends/holidays, so
    calendar time per bar exceeds the bar's own duration — we over-fetch and
    let warmup masking trim the head (cheap for one symbol; correctness over
    minimality).
    """
    secs = interval_seconds(source_agg)
    if source_agg == "1d":
        # ~252 trading days per 365 calendar; pad for holidays + safety.
        return timedelta(days=int(warmup_bars * 1.6) + 10)
    # Intraday: inflate ~3.5x for the closed hours each day, plus a weekend pad.
    return timedelta(seconds=int(warmup_bars * secs * 3.5)) + timedelta(days=4)


def _forward_fill_onto(source_series: pd.Series, display_index: pd.Index) -> pd.Series:
    """
    As-of (forward-fill) the coarser source MA onto the display bar
    timestamps: each display bar takes the most recent source MA value at or
    before it. This is the "step" that renders a daily MA flat across each
    intraday bar (thinkorswim semantics) and is the exact lookup the alert
    engine uses — chart and alert read identical numbers. Display bars before
    the first trusted source value stay NaN (→ None).
    """
    di = pd.DatetimeIndex(display_index)
    trusted = source_series.dropna()
    if trusted.empty or len(di) == 0:
        return pd.Series(index=di, dtype=float)

    # merge_asof needs both keys ascending. Sort the display index, map, then
    # restore original order so the result aligns to `display_index` 1:1.
    order = np.argsort(di.values, kind="stable")
    left = pd.DataFrame({"ts": di.values[order]})
    right = pd.DataFrame(
        {"ts": pd.DatetimeIndex(trusted.index).values, "val": trusted.to_numpy()}
    ).sort_values("ts")
    merged = pd.merge_asof(left, right, on="ts", direction="backward")

    vals_sorted = merged["val"].to_numpy()
    vals = np.empty(len(di), dtype=float)
    vals[order] = vals_sorted
    return pd.Series(vals, index=di)


def _pd_series_to_indicator_values(
    index: pd.Index, series: pd.Series,
) -> list[IndicatorValue]:
    """
    Zip the bar timestamps with the indicator values. NaN → None
    (consumers expect explicit nulls during warmup).

    `series` is assumed to be index-aligned to `index`. If lengths
    differ (e.g. ATR with min_periods), pandas reindex semantics
    fill the gap with NaN, which converts to None.
    """
    if len(series) != len(index):
        series = series.reindex(index)
    out: list[IndicatorValue] = []
    for ts, val in zip(index, series.to_list()):
        if val is None or (isinstance(val, float) and val != val):  # NaN check
            out.append(IndicatorValue(timestamp=ts, value=None))
        else:
            out.append(IndicatorValue(timestamp=ts, value=float(val)))
    return out


def _format_label(
    name: str,
    params: dict[str, Any],
    *,
    component: Optional[str] = None,
    source_agg: Optional[str] = None,
) -> str:
    """
    Default display label. 'SMA(20)', 'RSI(14)', 'BB Upper(20, 2.0)'.

    `component` (when set) signals a multi-output indicator's
    specific output. If component is prefixed with the indicator
    name (e.g. 'bollinger_upper'), the prefix is stripped so the
    label doesn't read 'BB Bollinger Upper'.

    `source_agg` (when set) appends the aggregation so a cross-timeframe
    MA is unambiguous on the chart: 'SMA(200) · 1d'.
    """
    name_lower = name.lower()
    name_pretty = {
        "sma": "SMA", "ema": "EMA", "wma": "WMA",
        "rsi": "RSI", "macd": "MACD", "tsi": "TSI",
        "stochastic": "Stoch",
        "atr": "ATR", "bollinger": "BB",
    }.get(name_lower, name.upper())
    params_str = ", ".join(str(v) for v in params.values()) if params else ""
    paren = f"({params_str})" if params_str else ""
    if component:
        # Strip 'bollinger_' from 'bollinger_upper' so the label is just 'BB Upper'.
        if component.lower().startswith(name_lower + "_"):
            component = component[len(name_lower) + 1 :]
        base = f"{name_pretty} {component.replace('_', ' ').title()}{paren}"
    else:
        base = f"{name_pretty}{paren}"
    if source_agg:
        base = f"{base} · {source_agg}"
    return base
