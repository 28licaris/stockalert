"""
Per-provider normalization to the split-adjusted canonical frame.

Bronze tables carry the prices each provider sent, in their native
adjustment state (verified by the 2026-05-17 probe):
  - bronze.polygon_minute:  RAW (unadjusted)
  - bronze.schwab_minute:   SPLIT_ADJUSTED

Silver carries ONE set of OHLCV columns — split-adjusted. This module
maps each provider's bronze rows INTO that canonical frame.

**Math** (see silver_layer_plan §2.9 + §3.3 for the design):

Given a bar at timestamp `T` for symbol `S`, define:

    F(S, T) = product of split.factor for every silver.corp_actions row
              where symbol = S, action_type = 'split', ex_date > date(T)

F is the cumulative forward-split factor for splits AFTER the bar's
calendar date. A 4-for-1 split on Aug 31 2020 contributes `factor=4`
to F for any bar with `date(T) < 2020-08-31`.

Then:

    Polygon (RAW → split-adjusted):
        out = input / F(S, T)
        (divide by post-bar splits to scale into the
         post-all-splits frame)

    Schwab (SPLIT_ADJUSTED → passthrough):
        out = input
        (already in the post-all-splits frame)

**Worked example.** NVDA had a 10-for-1 split on 2024-06-10.

  Bar at 2024-06-07 14:30 ET, the trader saw 1208.88 on screen.
  F = 10 (one 10-for-1 split AFTER this bar).

  Polygon bronze (raw=1208.88) → silver close = 1208.88 / 10 = 120.88
  Schwab bronze (adj=120.88)   → silver close = 120.88 (passthrough)
  Both reconcile to identical silver rows. ✓

  Bar at 2024-06-10 14:30 ET (split day), close = 121.79.
  F = 1 (no splits after this bar).

  Polygon bronze (raw=121.79)  → silver close = 121.79 / 1 = 121.79
  Schwab bronze (adj=121.79)   → silver close = 121.79
  Both identical. ✓

**If a consumer needs raw prices** (trade-tape replay): they multiply
silver's split-adjusted value by F(symbol, bar_date). The math + the
silver.corp_actions table are public. Silver intentionally does NOT
store both views — that was bloat (TA-5.1.8 cleanup, 2026-05-18).

**Cash dividend adjustment is not handled here.** Schwab's
pricehistory appears to only split-adjust (not dividend-adjust) per
the probe. If we ever add a `close_total_return` column for true
Yahoo-style total-return adjustment, the math expands.

**Volume handling:** split-adjustment multiplies volume too —
forward-split halves the share size, so post-split volume is
`bronze_volume × F` for Polygon (raw → post-split equivalent shares)
and passthrough for Schwab (already in post-split shares).
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
    """Map bronze rows into the silver canonical (split-adjusted) frame.

    Output dicts have ONE set of OHLCV columns (open/high/low/close/volume),
    always in the split-adjusted frame, plus `source_provider` passed
    through from the bronze `source` tag, plus the symbol + timestamp
    identifiers.

    `adjustment_status` decides the math direction:
      - ADJUSTMENT_STATUS_RAW: out = bronze / F (volume × F)
      - ADJUSTMENT_STATUS_SPLIT_ADJUSTED: out = bronze (passthrough)
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
            # Polygon (raw) → divide prices by F, multiply volume by F.
            open_norm = _div_or_none(open_v, F)
            high_norm = _div_or_none(high_v, F)
            low_norm = _div_or_none(low_v, F)
            close_norm = _div_or_none(close_v, F)
            volume_norm = _mul_int_or_none(volume_v, F)
        else:
            # Schwab (already split-adjusted) → passthrough.
            open_norm, high_norm, low_norm, close_norm = (
                open_v, high_v, low_v, close_v,
            )
            volume_norm = volume_v

        out.append({
            "symbol": symbol,
            "timestamp": ts,
            "open": open_norm,
            "high": high_norm,
            "low": low_norm,
            "close": close_norm,
            "volume": volume_norm,
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
    """Polygon raw price → split-adjusted: divide by F."""
    if v is None or F == 0:
        return None
    return v / F


def _mul_int_or_none(v, F: float):
    """Polygon raw volume → split-adjusted volume: multiply by F.
    Integer-result (volume can't be fractional)."""
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
    "schwab-tipfill": "schwab",  # TA-5.3.2 on-demand REST tip-fill
}


def _provider_from_source(source: str | None) -> str:
    """Map bronze's `source` value → canonical provider for the silver row.

    Falls back to `source` itself if not in the known map (operator
    override case via DATA_SOURCE_TAG).
    """
    if source is None:
        return "unknown"
    return _SOURCE_TO_PROVIDER.get(source, source)
