"""
Provider-precedence merge + bar-quality computation for silver OHLCV.

Takes normalized per-provider rows (from `normalize.py`) and:
  1. Merges them into one row per `(symbol, ts)` using the configured
     provider precedence (default `polygon > schwab`).
  2. Computes per-`(symbol, date)` bar-quality metrics for the
     `silver.bar_quality` audit ledger.

Both outputs are returned as PyArrow Tables matching the silver
Iceberg schemas — ready for `table.upsert()`.

**Why merge + quality in one module:** the merge step iterates every
provider's rows for the slice; that same iteration counts
participation, gaps, and disagreements for bar_quality. One pass,
two outputs.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Iterable, Optional

import pyarrow as pa

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Arrow schemas for the two output tables (must match Iceberg exactly)
# ─────────────────────────────────────────────────────────────────────


_SILVER_OHLCV_1M_ARROW = pa.schema([
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
    # OHLCV — split-adjusted canonical view.
    pa.field("open", pa.float64(), nullable=True),
    pa.field("high", pa.float64(), nullable=True),
    pa.field("low", pa.float64(), nullable=True),
    pa.field("close", pa.float64(), nullable=True),
    pa.field("volume", pa.int64(), nullable=True),
    pa.field("vwap", pa.float64(), nullable=True),
    pa.field("trade_count", pa.int64(), nullable=True),
    pa.field("source_provider", pa.string(), nullable=False),
    pa.field("sources_seen", pa.string(), nullable=True),       # CSV
    pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("ingestion_run_id", pa.string(), nullable=True),
])


_SILVER_BAR_QUALITY_ARROW = pa.schema([
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("date", pa.date32(), nullable=False),
    pa.field("expected_bars", pa.int32(), nullable=True),
    pa.field("actual_bars", pa.int32(), nullable=True),
    pa.field("gap_count", pa.int32(), nullable=True),
    pa.field("max_gap_minutes", pa.int32(), nullable=True),
    pa.field("providers_seen", pa.string(), nullable=True),     # CSV
    pa.field("disagreement_count", pa.int32(), nullable=True),
    pa.field("backfill_attempts", pa.int32(), nullable=True),
    pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("ingestion_run_id", pa.string(), nullable=True),
])


# How many minutes the US Regular Trading Hours session has, used for
# `expected_bars` in bar_quality. RTH = 9:30 ET to 16:00 ET = 6.5 hours
# = 390 minutes. Operators can override per-symbol later if a stock
# trades on a different schedule (ETFs with non-standard hours, ADRs,
# etc.); for now everything uses the default.
RTH_MINUTES_PER_DAY = 390


# Tolerance for price disagreement between providers (used by
# bar_quality). 50¢ OR 0.5% — same tolerance as the probe.
_DISAGREEMENT_DOLLAR = 0.50
_DISAGREEMENT_PCT = 0.005


# ─────────────────────────────────────────────────────────────────────
# Provider precedence merge
# ─────────────────────────────────────────────────────────────────────


def merge_with_precedence(
    per_provider_rows: list[tuple[str, list[dict]]],
    *,
    run_id: str,
) -> pa.Table:
    """Merge normalized per-provider rows → one silver row per (symbol, ts).

    `per_provider_rows` is `[(provider_name, [normalized_row, ...]), ...]`
    in PRECEDENCE ORDER (highest priority first). The first provider
    with a row for a given (symbol, ts) wins that cell. Subsequent
    providers' rows contribute to `sources_seen` and trigger
    disagreement-counter increments (counted separately in
    `compute_bar_quality`).

    Returns an Arrow Table matching `silver.ohlcv_1m`.
    """
    if not per_provider_rows:
        return _empty_ohlcv_arrow()

    # Build {(symbol, ts): winning_row} + a parallel
    # {(symbol, ts): set(providers_seen)} for the sources_seen column.
    winners: dict[tuple, dict] = {}
    seen: dict[tuple, set[str]] = defaultdict(set)

    for provider_name, rows in per_provider_rows:
        for row in rows:
            key = (row["symbol"], _coerce_ts(row["timestamp"]))
            seen[key].add(provider_name)
            if key not in winners:
                winners[key] = {**row, "_winner_provider": provider_name}

    # Stamp the final source_provider + sources_seen columns + audit metadata.
    ingestion_ts = datetime.now(timezone.utc)
    out_rows: list[dict] = []
    for key, row in winners.items():
        providers = sorted(seen[key])
        out = {**row}
        out["source_provider"] = row.get("source_provider", row["_winner_provider"])
        out["sources_seen"] = ",".join(providers)
        out["ingestion_ts"] = ingestion_ts
        out["ingestion_run_id"] = run_id
        # Drop the internal field
        out.pop("_winner_provider", None)
        out_rows.append(out)

    if not out_rows:
        return _empty_ohlcv_arrow()

    arrays = {col: [r.get(col) for r in out_rows] for col in _SILVER_OHLCV_1M_ARROW.names}
    return pa.Table.from_pydict(arrays, schema=_SILVER_OHLCV_1M_ARROW)


# ─────────────────────────────────────────────────────────────────────
# Bar-quality computation
# ─────────────────────────────────────────────────────────────────────


def compute_bar_quality(
    per_provider_rows: list[tuple[str, list[dict]]],
    *,
    run_id: str,
    expected_bars_per_day: int = RTH_MINUTES_PER_DAY,
) -> pa.Table:
    """Compute per-`(symbol, date)` quality metrics.

    Returns an Arrow Table matching `silver.bar_quality`.

    Metrics:
    - `expected_bars`: RTH minutes per trading day (default 390).
    - `actual_bars`: distinct minute timestamps observed in the merged
      slice for this (symbol, date).
    - `gap_count`: number of consecutive missing-minute runs in the
      slice (each contiguous absent block = 1 gap).
    - `max_gap_minutes`: largest such run.
    - `providers_seen`: CSV of provider names that contributed at
      least one row for this (symbol, date).
    - `disagreement_count`: number of (symbol, ts) cells where two or
      more providers' close prices differ by > tolerance.
    - `backfill_attempts`: placeholder, populated by the silver build
      orchestrator (TA-5.1.4) when it tracks retries.
    """
    if not per_provider_rows:
        return _empty_bar_quality_arrow()

    # Group rows by (symbol, date) across all providers.
    # Per (symbol, date): track {provider: {ts: close}} for disagreement
    # check, plus {ts: count} for gap analysis.
    grouped: dict[tuple, dict] = defaultdict(lambda: {
        "providers": set(),
        "per_provider_closes": defaultdict(dict),  # {provider: {ts: close}}
        "all_timestamps": set(),
    })

    for provider_name, rows in per_provider_rows:
        for row in rows:
            ts = _coerce_ts(row["timestamp"])
            d = ts.date()
            key = (row["symbol"], d)
            g = grouped[key]
            g["providers"].add(provider_name)
            g["per_provider_closes"][provider_name][ts] = _safe_float(row.get("close"))
            g["all_timestamps"].add(ts)

    ingestion_ts = datetime.now(timezone.utc)
    quality_rows: list[dict] = []

    for (symbol, d), data in grouped.items():
        timestamps = sorted(data["all_timestamps"])
        actual = len(timestamps)
        gap_count, max_gap = _count_gaps(timestamps)
        disagreements = _count_disagreements(data["per_provider_closes"])

        quality_rows.append({
            "symbol": symbol,
            "date": d,
            "expected_bars": expected_bars_per_day,
            "actual_bars": actual,
            "gap_count": gap_count,
            "max_gap_minutes": max_gap,
            "providers_seen": ",".join(sorted(data["providers"])),
            "disagreement_count": disagreements,
            "backfill_attempts": 0,   # set by orchestrator
            "ingestion_ts": ingestion_ts,
            "ingestion_run_id": run_id,
        })

    if not quality_rows:
        return _empty_bar_quality_arrow()

    arrays = {col: [r.get(col) for r in quality_rows] for col in _SILVER_BAR_QUALITY_ARROW.names}
    return pa.Table.from_pydict(arrays, schema=_SILVER_BAR_QUALITY_ARROW)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _count_gaps(timestamps: list[datetime]) -> tuple[int, int]:
    """Given sorted minute timestamps, count gap runs + max gap length.

    A "gap" here is any minute boundary that should have a bar but
    doesn't (within the same trading day). Returns (gap_count,
    max_gap_minutes).
    """
    if len(timestamps) <= 1:
        return 0, 0

    gap_count = 0
    max_gap = 0
    prev = timestamps[0]
    for ts in timestamps[1:]:
        delta_min = int((ts - prev).total_seconds() // 60)
        if delta_min > 1:
            gap_count += 1
            max_gap = max(max_gap, delta_min - 1)
        prev = ts
    return gap_count, max_gap


def _count_disagreements(
    per_provider_closes: dict[str, dict[datetime, Optional[float]]],
) -> int:
    """How many (ts, provider_pair) cells have close-price disagreement
    > tolerance?

    Counts each (ts) where any two providers disagree as ONE
    disagreement (not per-pair). So if 3 providers all disagree on
    the same ts, that's still 1 disagreement.
    """
    if len(per_provider_closes) < 2:
        return 0

    # Union of timestamps where at least 2 providers contributed
    all_ts: dict[datetime, list[float]] = defaultdict(list)
    for provider, ts_map in per_provider_closes.items():
        for ts, close in ts_map.items():
            if close is not None:
                all_ts[ts].append(close)

    disagreements = 0
    for ts, closes in all_ts.items():
        if len(closes) < 2:
            continue
        lo, hi = min(closes), max(closes)
        abs_diff = hi - lo
        pct_diff = abs_diff / hi if hi else 0
        if abs_diff > _DISAGREEMENT_DOLLAR and pct_diff > _DISAGREEMENT_PCT:
            disagreements += 1
    return disagreements


def _coerce_ts(ts) -> datetime:
    """tz-aware datetime; defend against naive."""
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


def _empty_ohlcv_arrow() -> pa.Table:
    return pa.Table.from_pydict(
        {col: [] for col in _SILVER_OHLCV_1M_ARROW.names},
        schema=_SILVER_OHLCV_1M_ARROW,
    )


def _empty_bar_quality_arrow() -> pa.Table:
    return pa.Table.from_pydict(
        {col: [] for col in _SILVER_BAR_QUALITY_ARROW.names},
        schema=_SILVER_BAR_QUALITY_ARROW,
    )
