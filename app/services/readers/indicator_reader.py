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
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from app.indicators.registry import get_indicator
from app.services.readers.schemas import (
    BronzeBar,
    IndicatorChartData,
    IndicatorSeries,
    IndicatorValue,
)

logger = logging.getLogger(__name__)


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
    ) -> IndicatorSeries:
        """
        Single-indicator query → single `IndicatorSeries`.

        For multi-output indicators (Bollinger / Stochastic / MACD)
        this returns only the canonical component (middle band /
        %K / MACD line — same as the indicator's `compute()` method).
        To get all components, use `get_chart_data` with one spec.
        """
        chart = self._compute_chart_data(
            symbol=symbol,
            indicator_specs=[{"name": indicator, "params": params, "label": label}],
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
            spec_params = dict(spec.get("params") or {})
            label_override = spec.get("label")
            try:
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
                    values=[], count=0,
                ))
                continue
            for component_name, pd_series in components.items():
                values = _pd_series_to_indicator_values(df.index, pd_series)
                # Multi-output: label per-component. Single-output:
                # honor the caller's label override if present.
                if component_name == name and label_override:
                    label = label_override
                else:
                    label = _format_label(name, spec_params, component=component_name if component_name != name else None)
                series_out.append(IndicatorSeries(
                    name=component_name, params=spec_params, label=label,
                    values=values, count=len(values),
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
    name: str, params: dict[str, Any], *, component: Optional[str] = None,
) -> str:
    """
    Default display label. 'SMA(20)', 'RSI(14)', 'BB Upper(20, 2.0)'.

    `component` (when set) signals a multi-output indicator's
    specific output. If component is prefixed with the indicator
    name (e.g. 'bollinger_upper'), the prefix is stripped so the
    label doesn't read 'BB Bollinger Upper'.
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
        return f"{name_pretty} {component.replace('_', ' ').title()}{paren}"
    return f"{name_pretty}{paren}"
