"""
Read-time split adjustment — the lean replacement for the materialized
``equities.polygon_adjusted`` table.

`adjusted = f(polygon_raw, market_corp_actions splits)`, so we don't store
a second copy of every bar; we compute the adjustment when the data is read.

This module is the SINGLE source of the adjustment math, lifted from the
Spark job ``scripts/spark/polygon_adjustment_job.py`` so the read-time path
reproduces the previously-materialized table **bit-for-bit**. It is reused
by ``AdjustedOhlcvReader`` (single-symbol reads), the bulk ``read_arrow``
path, and the equivalence gate.

Algorithm (mirrors the Spark job exactly):

  adj_factor(symbol, T) = ∏ split_factor_i   for splits with ex_date_i > date(T)

  - splits come from ``market_corp_actions`` WHERE action_type='split' AND
    factor IS NOT NULL AND factor != 1.0.
  - multiple splits on one ex_date collapse via sum-of-logs.
  - the cumulative product is computed as ``exp(reverse_cumsum(log factor))``
    in DESCending ex_date accumulation order — the SAME order Spark's
    ``exp(sum(log(factor)) over (order by ex_date desc))`` uses, so the
    float64 result matches to the last ULP.
  - per bar: ``searchsorted(ex_dates_asc, bar_utc_date, side='right')`` →
    strict ``ex_date > bar_date``. Bars ON the split day are post-split.
  - bar date = the bar timestamp normalized to **UTC midnight** (matches the
    Spark job's ``pd.to_datetime(ts).dt.normalize()`` on UTC-naive
    timestamps). NOTE: this is intentionally UTC-based, not ET — it
    reproduces the existing job; an ET refinement would change values and is
    out of scope for the lean-storage migration.

Adjustment applied (mirrors Spark ``select`` block):
  open/high/low/close ÷ adj_factor ; volume × adj_factor ;
  vwap + trade_count PASS THROUGH UNCHANGED ; source := 'polygon-adjusted'.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

import numpy as np
import pyarrow as pa

# Output column order = POLYGON_ADJUSTED_SCHEMA (so consumers/readers that
# expect the adjusted shape keep working unchanged).
ADJUSTED_COLUMNS = (
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "vwap", "trade_count", "source", "adj_factor",
)

_ADJUSTED_SOURCE = "polygon-adjusted"

# Per-symbol lookup: symbol -> (ex_dates_asc datetime64[ns], cum_factor float64).
CumFactorLookup = Mapping[str, "tuple[np.ndarray, np.ndarray]"]


def build_cum_factor_lookup(
    splits: Iterable[tuple[str, "date | np.datetime64", float]],
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Build the per-symbol cumulative-future-split lookup.

    `splits` is an iterable of ``(symbol, ex_date, factor)`` — typically the
    ``market_corp_actions`` rows already filtered to ``action_type='split'``
    (the caller does the filter so this stays a pure transform).

    Returns ``{symbol: (ex_dates_asc, cum_factors)}`` where ``cum_factors[i]``
    is the product of every split factor with ``ex_date >= ex_dates_asc[i]``
    (i.e. "this split and all later ones"). A bar gets ``cum_factors[idx]``
    where ``idx`` is the first ex_date strictly greater than the bar's date.

    Computation matches the Spark job's ``exp(sum(log(factor)))`` so values
    are float64-identical: collapse same-day splits by summing logs, then
    reverse-cumsum the logs (DESC accumulation) and ``exp``.
    """
    # 1. Collapse to sum-of-logs per (symbol, ex_date); drop no-op factors.
    by_key: dict[tuple[str, np.datetime64], float] = {}
    for symbol, ex_date, factor in splits:
        if factor is None or factor == 1.0:
            continue
        if symbol is None or ex_date is None:
            continue
        ed = np.datetime64(ex_date, "ns")
        key = (symbol, ed)
        by_key[key] = by_key.get(key, 0.0) + float(np.log(factor))

    # 2. Group by symbol, sort ex_date ASC, reverse-cumsum the logs → exp.
    per_symbol: dict[str, list[tuple[np.datetime64, float]]] = {}
    for (symbol, ed), lf in by_key.items():
        per_symbol.setdefault(symbol, []).append((ed, lf))

    lookup: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for symbol, rows in per_symbol.items():
        rows.sort(key=lambda r: r[0])  # ascending ex_date
        ex_dates = np.array([r[0] for r in rows], dtype="datetime64[ns]")
        lf_asc = np.array([r[1] for r in rows], dtype=np.float64)
        # reverse-cumsum (sum lf[j] for j >= i), accumulated DESC like Spark.
        rev_csum = np.cumsum(lf_asc[::-1])[::-1]
        cum = np.exp(rev_csum)
        lookup[symbol] = (ex_dates, cum)
    return lookup


def adj_factors_for(
    symbols: np.ndarray,
    bar_dates: np.ndarray,
    lookup: CumFactorLookup,
) -> np.ndarray:
    """Vectorized per-bar adj_factor.

    `symbols` and `bar_dates` (datetime64[ns], UTC-midnight-normalized) are
    parallel arrays, one entry per bar. Returns a float64 adj_factor array
    (1.0 where the symbol has no qualifying future split).
    """
    out = np.ones(len(bar_dates), dtype=np.float64)
    if not lookup:
        return out
    # Group row indices by symbol so each symbol's searchsorted is one
    # vectorized call (common reader case = a single symbol = one call).
    order = np.argsort(symbols, kind="stable")
    sorted_syms = symbols[order]
    # boundaries of equal-symbol runs
    uniq, starts = np.unique(sorted_syms, return_index=True)
    starts = list(starts) + [len(sorted_syms)]
    for u_i, sym in enumerate(uniq):
        entry = lookup.get(sym)
        if entry is None:
            continue
        ex_dates, cum = entry
        idx_rows = order[starts[u_i]:starts[u_i + 1]]
        pos = np.searchsorted(ex_dates, bar_dates[idx_rows], side="right")
        valid = pos < len(ex_dates)
        factors = np.where(valid, cum[np.clip(pos, 0, len(ex_dates) - 1)], 1.0)
        out[idx_rows] = factors
    return out


def apply_adjustment(raw: pa.Table, lookup: CumFactorLookup) -> pa.Table:
    """Apply split adjustment to a raw OHLCV Arrow table.

    Input `raw` must carry the canonical columns (symbol, timestamp, open,
    high, low, close, volume, vwap, trade_count). Returns a new Arrow table
    in ``ADJUSTED_COLUMNS`` order with prices/volume adjusted and an
    ``adj_factor`` column — the same shape the materialized
    ``polygon_adjusted`` exposed (minus the ingestion bookkeeping columns,
    which are write-time metadata, not part of the bar contract).
    """
    n = raw.num_rows
    if n == 0:
        return _empty_adjusted()

    symbols = raw.column("symbol").to_numpy(zero_copy_only=False)

    # bar date = UTC-midnight of the timestamp, as naive datetime64[ns]
    # (matches the Spark partition fn). PyIceberg timestamptz → arrow
    # timestamp[us,UTC]; cast to naive UTC ns then floor to day.
    ts = raw.column("timestamp")
    ts_ns = ts.cast(pa.timestamp("ns")).to_numpy(zero_copy_only=False)
    bar_dates = ts_ns.astype("datetime64[D]").astype("datetime64[ns]")

    factor = adj_factors_for(symbols, bar_dates, lookup)

    def _f(col: str) -> np.ndarray:
        return raw.column(col).to_numpy(zero_copy_only=False).astype(np.float64)

    open_ = _f("open") / factor
    high = _f("high") / factor
    low = _f("low") / factor
    close = _f("close") / factor
    volume = _f("volume") * factor

    return pa.table({
        "symbol": raw.column("symbol"),
        "timestamp": raw.column("timestamp"),
        "open": pa.array(open_, type=pa.float64()),
        "high": pa.array(high, type=pa.float64()),
        "low": pa.array(low, type=pa.float64()),
        "close": pa.array(close, type=pa.float64()),
        "volume": pa.array(volume, type=pa.float64()),
        "vwap": raw.column("vwap"),
        "trade_count": raw.column("trade_count"),
        "source": pa.array([_ADJUSTED_SOURCE] * n, type=pa.string()),
        "adj_factor": pa.array(factor, type=pa.float64()),
    })


def _empty_adjusted() -> pa.Table:
    return pa.table({
        "symbol": pa.array([], pa.string()),
        "timestamp": pa.array([], pa.timestamp("us", tz="UTC")),
        "open": pa.array([], pa.float64()),
        "high": pa.array([], pa.float64()),
        "low": pa.array([], pa.float64()),
        "close": pa.array([], pa.float64()),
        "volume": pa.array([], pa.float64()),
        "vwap": pa.array([], pa.float64()),
        "trade_count": pa.array([], pa.int64()),
        "source": pa.array([], pa.string()),
        "adj_factor": pa.array([], pa.float64()),
    })
