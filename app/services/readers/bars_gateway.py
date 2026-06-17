"""Unified chart-bars access — ClickHouse (hot cache) ⟷ S3 lake (ground truth).

ONE place that owns the CH-vs-S3 routing for chart bars, called by BOTH
the HTTP `/api/v1/bars` route and the MCP `get_bars_for_chart` tool so
the dashboard and agents behave identically — no drift between surfaces.

Data-tier model:
  - **S3 lake** (`equities.polygon_adjusted`) is ground truth: every
    symbol, full 20-year split-adjusted history. Authoritative but slow
    to scan (Parquet over the network).
  - **ClickHouse** (`stocks.ohlcv_1m`) is a hot snapshot cache of the
    active universe. Fast, but partial — it lives on a local box and
    only holds what's been streamed or filled. Not authoritative.

Sources (the `source` selector):
  - ``auto`` (default): CH-first. On an empty result for a bounded
    window, fill that window from the lake into CH and re-query CH.
    Self-healing cache — the second identical request is hot. This is
    effectively "CH plus S3 for whatever's missing."
  - ``clickhouse``: CH only. Fast, may be partial. Never touches S3.
    Use when you explicitly want "only what's hot."
  - ``lake``: S3 ground truth only. Reads 1-minute adjusted bars from
    `equities.polygon_adjusted` and resamples to `interval` in-process.
    Does NOT write CH — the no-side-effect escape hatch for deep history
    / agent analysis when you don't want to warm (bloat) the local CH.

All three return ``list[LiveBar]`` so callers adapt one shape regardless
of where the data came from.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from app.services.readers.bar_reader import BarReader
from app.services.readers.schemas import LiveBar

logger = logging.getLogger(__name__)


class BarSource(str, Enum):
    """Where chart bars come from. String-valued so it round-trips as a
    query param / MCP arg without custom coercion."""

    AUTO = "auto"
    CLICKHOUSE = "clickhouse"
    LAKE = "lake"


# Minutes per display interval — for resampling lake 1m bars (the CH path
# resamples server-side via toStartOfInterval; the lake hands back raw 1m).
_INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440,
}

# Only fill CH for bounded windows — an unbounded request would drag the
# full 20-year scan into the hot path. 365d matches the route's prior cap.
_MAX_FILL_LOOKBACK_DAYS = 365


def get_chart_bars(
    symbol: str,
    *,
    interval: str = "1m",
    lookback_days: Optional[int] = None,
    limit: Optional[int] = None,
    source: BarSource = BarSource.AUTO,
    reader: Optional[BarReader] = None,
) -> list[LiveBar]:
    """Return chart bars for `symbol`, routing across CH and the S3 lake.

    See the module docstring for the `source` semantics. `reader` is the
    ClickHouse `BarReader` (injected by callers / tests); built from
    settings when omitted. Raises `ValueError` on an unknown interval
    (propagated from the reader / resampler).
    """
    if interval not in _INTERVAL_MINUTES:
        raise ValueError(
            f"Unknown interval {interval!r}. "
            f"Supported: {sorted(_INTERVAL_MINUTES)}."
        )

    if source == BarSource.LAKE:
        return _from_lake(symbol, interval=interval, lookback_days=lookback_days, limit=limit)

    ch_reader = reader or BarReader.from_settings()
    bars = ch_reader.get_bars_for_chart(
        symbol, interval=interval, lookback_days=lookback_days, limit=limit,
    )

    if source == BarSource.CLICKHOUSE:
        return bars

    # AUTO: CH-first; fill from the lake on an empty bounded window, re-query.
    if (
        not bars
        and lookback_days is not None
        and lookback_days <= _MAX_FILL_LOOKBACK_DAYS
    ):
        try:
            from app.services.equities.lake_to_ch_fill import fill_ch_from_lake_sync

            end = datetime.now(timezone.utc)
            start = end - timedelta(days=lookback_days)
            inserted = fill_ch_from_lake_sync(symbol.upper(), start, end)
            if inserted > 0:
                logger.info(
                    "bars_gateway: lake-filled %s (%d rows, %dd window); re-querying CH",
                    symbol, inserted, lookback_days,
                )
                bars = ch_reader.get_bars_for_chart(
                    symbol, interval=interval,
                    lookback_days=lookback_days, limit=limit,
                )
        except Exception as exc:  # noqa: BLE001 — boundary; degrade to CH result
            logger.warning("bars_gateway: lake fill for %s failed: %s", symbol, exc)

    return bars


def _from_lake(
    symbol: str,
    *,
    interval: str,
    lookback_days: Optional[int],
    limit: Optional[int],
) -> list[LiveBar]:
    """Read 1m adjusted bars from the S3 lake and resample to `interval`.

    Ground-truth path — no CH read or write. Window defaults to the last
    `lookback_days` (or 30d when omitted, to bound the scan).
    """
    from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days if lookback_days is not None else 30)

    reader = AdjustedOhlcvReader.from_settings()
    resp = reader.get_bars(symbol, start, end)

    one_min = [
        LiveBar(
            symbol=b.symbol,
            timestamp=b.timestamp,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=float(b.volume),
            vwap=b.vwap,
            trade_count=b.trade_count,
            source="lake-polygon_adjusted",
            interval="1m",
        )
        for b in resp.bars
    ]

    bars = _resample(one_min, interval)
    if limit is not None and len(bars) > limit:
        bars = bars[-limit:]  # newest-anchored, like the CH auto-limit
    return bars


def _resample(bars: list[LiveBar], interval: str) -> list[LiveBar]:
    """Aggregate 1-minute `LiveBar`s into `interval` buckets (OHLCV)."""
    minutes = _INTERVAL_MINUTES[interval]
    if minutes <= 1 or not bars:
        return bars

    bucket_s = minutes * 60
    out: list[LiveBar] = []
    cur: Optional[LiveBar] = None
    cur_key: Optional[int] = None

    for b in bars:
        epoch = int(b.timestamp.timestamp())
        key = epoch - (epoch % bucket_s)
        if cur is None or key != cur_key:
            if cur is not None:
                out.append(cur)
            cur_key = key
            cur = LiveBar(
                symbol=b.symbol,
                timestamp=datetime.fromtimestamp(key, tz=timezone.utc),
                open=b.open, high=b.high, low=b.low, close=b.close,
                volume=b.volume,
                vwap=b.vwap,
                trade_count=b.trade_count,
                source=b.source,
                interval=interval,
            )
        else:
            cur.high = max(cur.high, b.high)
            cur.low = min(cur.low, b.low)
            cur.close = b.close
            cur.volume += b.volume
            if b.trade_count is not None:
                cur.trade_count = (cur.trade_count or 0) + b.trade_count
    if cur is not None:
        out.append(cur)
    return out
