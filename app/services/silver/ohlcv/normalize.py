"""
Per-provider raw↔split-adjusted normalization.

Bronze tables carry the prices each provider sent, in their native
adjustment state (verified by the 2026-05-17 probe):
  - bronze.polygon_minute:  RAW (unadjusted)
  - bronze.schwab_minute:   SPLIT_ADJUSTED

Silver carries BOTH `_raw` and `_adj` for every row. This module
performs the per-provider transformation that fills in the missing
column.

**Math** (see silver_layer_plan §2.9 + §3.3 for the design):

Given a bar at timestamp `T` for symbol `S`, define:

    F(S, T) = product of split.factor for every silver.corp_actions row
              where symbol = S, action_type = 'split', ex_date > date(T)

That is, F is the cumulative forward-split factor for splits AFTER the
bar's calendar date. A 4-for-1 split on Aug 31 2020 contributes
`factor=4` to F for any bar with `date(T) < 2020-08-31`.

Then:

    Polygon (raw → both):
        _raw = passthrough (provider's value)
        _adj = _raw / F(S, T)
            (divide by post-bar splits to scale into the post-all-splits frame)

    Schwab (split_adjusted → both):
        _adj = passthrough (provider's already-adjusted value)
        _raw = _adj × F(S, T)
            (multiply back to undo the adjustment Schwab already applied)

**Worked example.** NVDA had a 10-for-1 split on 2024-06-10.

  Bar at 2024-06-07 14:30 ET, close = 1208.88 raw (= 120.88 split-adj).
  F = 10 (one 10-for-1 split AFTER this bar).

  Polygon bronze (raw):       Schwab bronze (split-adjusted):
    raw = 1208.88                adj = 120.88
    adj = 1208.88 / 10            raw = 120.88 × 10
        = 120.88                       = 1208.88

  Both providers produce identical silver rows. ✓

  Bar at 2024-06-10 14:30 ET (split day), close = 121.79.
  F = 1 (no splits after this bar).

  Polygon bronze (raw=121.79):  Schwab bronze (adj=121.79):
    raw = 121.79                  adj = 121.79
    adj = 121.79 / 1              raw = 121.79 × 1
        = 121.79                       = 121.79

  Both identical. ✓

**Cash dividend adjustment is not handled here.** Schwab's
pricehistory appears to only split-adjust (not dividend-adjust) per
the probe. If we ever add a `close_div_adj` column for true
Yahoo-style total-return adjustment, the math expands. For now,
`_adj` means "split-adjusted only".

**Volume handling:** split-adjustment multiplies volume too —
forward-split halves the share size, so volume in "post-split shares"
is `volume_raw × F`. We mirror the same scaling.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Iterable

import pyarrow as pa

from app.services.bronze.schemas import (
    ADJUSTMENT_STATUS_RAW,
    ADJUSTMENT_STATUS_SPLIT_ADJUSTED,
)

logger = logging.getLogger(__name__)


# Type alias: per-symbol → ordered list of (ex_date, factor) for splits only.
# Sorted ascending by ex_date so the cumulative-product loop below can
# do a single scan per symbol.
SplitFactors = dict[str, list[tuple[date, float]]]


def build_split_factor_index(corp_actions_arrow: pa.Table) -> SplitFactors:
    """Reduce silver.corp_actions PyArrow → per-symbol list of (ex_date, factor).

    Only `action_type == 'split'` rows contribute. Output is sorted by
    ex_date ASC per symbol.

    Cash dividends, capital gains, etc. are intentionally NOT included —
    silver's `_adj` columns are SPLIT-adjusted only (matches Schwab's
    behavior; matches what backtests need to avoid fake split-day
    discontinuities). Dividend adjustment is a future
    `close_div_adj` column if we ever need it.
    """
    by_symbol: SplitFactors = defaultdict(list)
    if corp_actions_arrow is None or corp_actions_arrow.num_rows == 0:
        return dict(by_symbol)

    rows = corp_actions_arrow.to_pylist()
    for r in rows:
        if r.get("action_type") != "split":
            continue
        factor = r.get("factor")
        if factor is None or factor <= 0:
            continue
        by_symbol[r["symbol"]].append((r["ex_date"], float(factor)))

    # Sort each symbol's splits by ex_date ascending.
    for sym in by_symbol:
        by_symbol[sym].sort(key=lambda x: x[0])
    return dict(by_symbol)


def cumulative_factor_after(
    symbol: str,
    bar_date: date,
    split_index: SplitFactors,
) -> float:
    """Return product of all `split.factor` for `symbol` where
    `ex_date > bar_date`.

    Returns 1.0 if the symbol has no splits or all splits are on or
    before `bar_date`. Used by both directions (Polygon raw→adj
    divides by this; Schwab adj→raw multiplies by this).

    A bar ON the split day itself (`bar_date == ex_date`) does NOT
    get the factor applied — it's already in the post-split frame.
    """
    factors = split_index.get(symbol)
    if not factors:
        return 1.0
    product = 1.0
    for ex_date, f in factors:
        if ex_date > bar_date:
            product *= f
    return product


# ─────────────────────────────────────────────────────────────────────
# Per-provider transformations
# ─────────────────────────────────────────────────────────────────────


def normalize_provider_rows(
    rows: list[dict],
    *,
    adjustment_status: str,
    split_index: SplitFactors,
) -> list[dict]:
    """Take bronze rows + per-symbol split index → list of normalized
    dicts ready for the precedence merge.

    Each output dict has both `_raw` (open/high/low/close/volume) and
    `_adj` populated, plus `source_provider` (passed through from the
    bronze row's `source` field), plus the timestamp + symbol
    identifiers.

    `adjustment_status` decides the math direction:
      - ADJUSTMENT_STATUS_RAW: raw is passthrough; adj = raw / F
      - ADJUSTMENT_STATUS_SPLIT_ADJUSTED: adj is passthrough; raw = adj * F
    """
    if adjustment_status not in (
        ADJUSTMENT_STATUS_RAW, ADJUSTMENT_STATUS_SPLIT_ADJUSTED,
    ):
        raise ValueError(
            f"Unknown adjustment_status {adjustment_status!r}. "
            f"Expected one of: raw, split_adjusted."
        )

    out: list[dict] = []
    for r in rows:
        symbol = r["symbol"]
        ts = _coerce_ts(r["timestamp"])
        bar_date = ts.date()
        F = cumulative_factor_after(symbol, bar_date, split_index)

        open_v = _safe_float(r.get("open"))
        high_v = _safe_float(r.get("high"))
        low_v = _safe_float(r.get("low"))
        close_v = _safe_float(r.get("close"))
        volume_v = _safe_int(r.get("volume"))

        if adjustment_status == ADJUSTMENT_STATUS_RAW:
            open_raw, high_raw, low_raw, close_raw = open_v, high_v, low_v, close_v
            volume_raw = volume_v
            open_adj = _div_or_none(open_v, F)
            high_adj = _div_or_none(high_v, F)
            low_adj = _div_or_none(low_v, F)
            close_adj = _div_or_none(close_v, F)
            volume_adj = _mul_int_or_none(volume_v, F)   # split-adj volume = raw × F
        else:  # split_adjusted
            open_adj, high_adj, low_adj, close_adj = open_v, high_v, low_v, close_v
            volume_adj = volume_v
            open_raw = _mul_or_none(open_v, F)
            high_raw = _mul_or_none(high_v, F)
            low_raw = _mul_or_none(low_v, F)
            close_raw = _mul_or_none(close_v, F)
            volume_raw = _div_int_or_none(volume_v, F)   # raw volume = adj / F

        out.append({
            "symbol": symbol,
            "timestamp": ts,
            "open_raw": open_raw,
            "high_raw": high_raw,
            "low_raw": low_raw,
            "close_raw": close_raw,
            "volume_raw": volume_raw,
            "open_adj": open_adj,
            "high_adj": high_adj,
            "low_adj": low_adj,
            "close_adj": close_adj,
            "volume_adj": volume_adj,
            "vwap": _safe_float(r.get("vwap")),
            "trade_count": _safe_int(r.get("trade_count")),
            # Source provider is the row's bronze `source` tag mapped
            # back to the canonical provider name. e.g.
            # "polygon-flatfiles" → "polygon"; "schwab-stream" or
            # "schwab" → "schwab".
            "source_provider": _provider_from_source(r.get("source")),
        })
    return out


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _coerce_ts(ts) -> datetime:
    """Bronze gives us tz-aware datetime; defend against naive input."""
    from datetime import timezone
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    return datetime.fromisoformat(str(ts)).replace(tzinfo=timezone.utc)


def _safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _div_or_none(v, F: float):
    if v is None or F == 0:
        return None
    return v / F


def _mul_or_none(v, F: float):
    if v is None:
        return None
    return v * F


def _div_int_or_none(v, F: float):
    """Integer-result division. Rounds to nearest int (volume can't
    be fractional)."""
    if v is None or F == 0:
        return None
    return int(round(v / F))


def _mul_int_or_none(v, F: float):
    """Integer-result multiplication (volume scaling)."""
    if v is None:
        return None
    return int(round(v * F))


# ─────────────────────────────────────────────────────────────────────
# Source tag → canonical provider name
# ─────────────────────────────────────────────────────────────────────


_SOURCE_TO_PROVIDER = {
    "polygon-flatfiles": "polygon",
    "polygon-rest": "polygon",
    "polygon": "polygon",
    "schwab": "schwab",
    "schwab-stream": "schwab",
}


def _provider_from_source(source: str | None) -> str:
    """Map bronze's `source` value → canonical provider for the silver row.

    Falls back to `source` itself if not in the known map (operator
    override case via DATA_SOURCE_TAG).
    """
    if source is None:
        return "unknown"
    return _SOURCE_TO_PROVIDER.get(source, source)
