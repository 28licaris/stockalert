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
  - ``auto`` (default): CH-first. Returns CH bars immediately. When CH
    doesn't cover the requested window, schedules a background fill
    (gap_fill_worker) and returns what CH has now — the caller gets a
    response in <100ms and the next identical request will be hot.
  - ``clickhouse``: CH only. Fast, always partial. Never touches S3.
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

from app.services.futures.symbols import ch_table_for, is_futures_symbol
from app.services.readers.bar_reader import BarReader
from app.services.readers.schemas import LiveBar

logger = logging.getLogger(__name__)


def _lake_fill_fn(symbol: str):
    """The bounded-window lake→CH fill for a symbol's asset class.

    Futures fill from ``futures.schwab_futures`` → ``stocks.futures_ohlcv_1m``;
    equities from the polygon∪schwab union → ``stocks.ohlcv_1m``. Both share
    the ``(symbol, start, end) -> rows_inserted`` signature."""
    if is_futures_symbol(symbol):
        from app.services.futures.lake_to_ch_fill import fill_ch_from_futures_lake_sync
        return fill_ch_from_futures_lake_sync
    from app.services.equities.lake_to_ch_fill import fill_ch_from_lake_sync
    return fill_ch_from_lake_sync


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

# CH is considered to lack the requested depth (→ fill) when its earliest
# bar starts more than this many days AFTER the window start. The buffer
# absorbs a legit market-closed run at the very start of the window
# (long weekend / holiday) so we don't refill on every load. A real depth
# gap (CH loaded 3mo, window asks 1yr) blows past it.
_COVERAGE_BUFFER_DAYS = 4


def _ch_lacks_window(bars, window_start: datetime) -> bool:
    """True when CH's coverage doesn't reach back to `window_start` — either
    empty, or its earliest bar starts > _COVERAGE_BUFFER_DAYS after it.
    `bars` is oldest-first (so bars[0] is the earliest)."""
    if not bars:
        return True
    earliest = bars[0].timestamp
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    return earliest > window_start + timedelta(days=_COVERAGE_BUFFER_DAYS)


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

    # Futures daily is served from the deep daily lake tier for EVERY source —
    # the 1-minute-derived CH/lake only covers ~48 days, so the CH path would
    # hand back a stub. Falls through to CH only if the daily table is empty.
    if is_futures_symbol(symbol) and interval == "1d":
        daily = _from_lake(symbol, interval="1d", lookback_days=lookback_days, limit=limit)
        if daily:
            return daily

    table = ch_table_for(symbol)
    ch_reader = reader or BarReader.from_settings()
    bars = ch_reader.get_bars_for_chart(
        symbol, interval=interval, lookback_days=lookback_days, limit=limit,
        source_table=table,
    )

    if source == BarSource.CLICKHOUSE:
        return bars

    # AUTO: CH-first. If CH doesn't cover the window, schedule a background
    # fill and return what CH has immediately. The fill lands asynchronously;
    # the next request (after the fill completes) will see the full window.
    # Mid-session holes aren't fillable here — ch_reconcile handles those.
    if (
        lookback_days is not None
        and lookback_days <= _MAX_FILL_LOOKBACK_DAYS
    ):
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        if _ch_lacks_window(bars, start):
            from app.services.live.gap_fill_worker import schedule_gap_fill
            schedule_gap_fill(symbol, start, end)
            logger.info(
                "bars_gateway: CH lacks window for %s (%dd); gap fill scheduled",
                symbol, lookback_days,
            )

    return bars


def get_range_bars(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    interval: str = "1m",
    limit: Optional[int] = None,
    source: BarSource = BarSource.AUTO,
    reader: Optional[BarReader] = None,
) -> list[LiveBar]:
    """CH-first bars for an explicit ``[start, end)`` window, self-healing
    from the lake on a coverage gap. The window-based peer of
    `get_chart_bars`, used by the MCP `get_bars_in_range` tool so it
    behaves like the rest of the bars surface.

    Every interval is resampled from ``ohlcv_1m``; AUTO fills (and re-queries)
    that table from the lake when CH doesn't reach back to ``start``.
    """
    table = ch_table_for(symbol)
    ch_reader = reader or BarReader.from_settings()
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    kwargs = {"interval": interval, "source_table": table}
    if limit is not None:
        kwargs["limit"] = limit
    bars = ch_reader.get_bars_in_range(symbol, start, end, **kwargs)
    if source == BarSource.CLICKHOUSE:
        return bars

    window_days = (end - start).days
    if (
        source == BarSource.AUTO
        and 0 < window_days <= _MAX_FILL_LOOKBACK_DAYS
        and _ch_lacks_window(bars, start)
    ):
        from app.services.live.gap_fill_worker import schedule_gap_fill
        schedule_gap_fill(symbol, start, end)
        logger.info(
            "bars_gateway: CH lacks window for %s (%dd); gap fill scheduled",
            symbol, window_days,
        )

    return bars


def _from_lake(
    symbol: str,
    *,
    interval: str,
    lookback_days: Optional[int],
    limit: Optional[int],
) -> list[LiveBar]:
    """Read 1m bars from the S3 lake and resample to `interval`.

    Ground-truth path — no CH read or write. Futures read from
    ``futures.schwab_futures``; equities from the split-adjusted
    ``equities.polygon_adjusted``. Window defaults to the last
    `lookback_days` (or 30d when omitted, to bound the scan).
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days if lookback_days is not None else 30)

    if is_futures_symbol(symbol):
        # Daily+ intervals: prefer the deep daily tier (futures.schwab_futures_daily,
        # years of history) over resampling the ~48-day 1-minute window. Falls
        # through to the 1m resample if the daily table is empty / not built yet.
        if interval == "1d":
            daily = _futures_daily_from_lake(symbol, start, end)
            if daily:
                if limit is not None and len(daily) > limit:
                    daily = daily[-limit:]
                return daily
        one_min = _futures_one_min_from_lake(symbol, start, end)
    else:
        from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader

        resp = AdjustedOhlcvReader.from_settings().get_bars(symbol, start, end)
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


def _futures_one_min_from_lake(
    symbol: str, start: datetime, end: datetime
) -> list[LiveBar]:
    """1-min ``LiveBar``s for a futures root from ``futures.schwab_futures``,
    oldest-first. Empty list on no-data / lake error (logged upstream)."""
    from app.services.futures.lake_to_ch_fill import _scan_futures_lake

    arr = _scan_futures_lake(symbol.upper(), start, end)
    if arr is None or arr.num_rows == 0:
        return []

    cols = arr.to_pydict()
    out: list[LiveBar] = []
    for i in range(arr.num_rows):
        vwap = cols["vwap"][i]
        tc = cols["trade_count"][i]
        out.append(
            LiveBar(
                symbol=cols["symbol"][i],
                timestamp=cols["timestamp"][i],
                open=float(cols["open"][i]),
                high=float(cols["high"][i]),
                low=float(cols["low"][i]),
                close=float(cols["close"][i]),
                volume=float(cols["volume"][i]) if cols["volume"][i] is not None else 0.0,
                vwap=float(vwap) if vwap not in (None, 0, 0.0) else None,
                trade_count=int(tc) if tc not in (None, 0) else None,
                source="lake-schwab_futures",
                interval="1m",
            )
        )
    out.sort(key=lambda b: b.timestamp)  # lake scan isn't guaranteed ordered
    return out


def _futures_daily_from_lake(
    symbol: str, start: datetime, end: datetime
) -> list[LiveBar]:
    """Daily ``LiveBar``s for a futures root from ``futures.schwab_futures_daily``,
    oldest-first. Empty list when the table doesn't exist yet or on a read error
    (so callers fall back to resampling the 1-minute lake)."""
    from pyiceberg.exceptions import NoSuchTableError
    from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan

    from app.services.futures.schemas import futures_table_id
    from app.services.iceberg_catalog import get_catalog

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    try:
        table = get_catalog().load_table(futures_table_id("schwab_futures_daily"))
        arr = table.scan(
            row_filter=And(
                EqualTo("symbol", symbol.upper()),
                And(GreaterThanOrEqual("timestamp", start.isoformat()),
                    LessThan("timestamp", end.isoformat())),
            ),
            selected_fields=("symbol", "timestamp", "open", "high", "low", "close",
                             "volume", "vwap", "trade_count"),
        ).to_arrow()
    except NoSuchTableError:
        return []
    except Exception as exc:  # noqa: BLE001 — degrade to the 1m path
        logger.warning("bars_gateway: futures daily lake read failed for %s: %s", symbol, exc)
        return []

    if arr is None or arr.num_rows == 0:
        return []
    cols = arr.to_pydict()
    out: list[LiveBar] = []
    for i in range(arr.num_rows):
        vwap = cols["vwap"][i]
        tc = cols["trade_count"][i]
        out.append(
            LiveBar(
                symbol=cols["symbol"][i], timestamp=cols["timestamp"][i],
                open=float(cols["open"][i]), high=float(cols["high"][i]),
                low=float(cols["low"][i]), close=float(cols["close"][i]),
                volume=float(cols["volume"][i]) if cols["volume"][i] is not None else 0.0,
                vwap=float(vwap) if vwap not in (None, 0, 0.0) else None,
                trade_count=int(tc) if tc not in (None, 0) else None,
                source="lake-schwab_futures_daily", interval="1d",
            )
        )
    out.sort(key=lambda b: b.timestamp)
    return out


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
