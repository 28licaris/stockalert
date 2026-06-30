"""
Price-vs-moving-average crossover alerts.

Pure detector + an on-demand scan that reads the MA from the shared
`IndicatorReader` — so the value a crossover fires on is byte-identical to
the line drawn on the chart, across any source aggregation. A daily 200-SMA
crossover on a 5m chart is detected against the same forward-filled series
the user sees.

Two guarantees the spec ("Alert Semantics") demands:
  - Every alert carries `{ma, source_agg, length, display_agg}` so it stays
    interpretable later.
  - Warmup never fires: a crossover needs a valid MA on BOTH the prior and
    current bar, and warmup bars are masked to None by the reader — so a
    half-formed average cannot trigger.

Live wiring (a debounced bar-subscriber, like `IntradayWaveScanner`) reuses
`detect_crossings` unchanged; this module owns the detection + on-demand scan.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Sequence

from app.services.alerts.schemas import MACrossoverAlert
from app.services.readers.indicator_reader import IndicatorReader

logger = logging.getLogger(__name__)

_MA_NAMES = {"sma", "ema", "wma"}


def detect_crossings(
    price: Sequence[Optional[float]],
    ma: Sequence[Optional[float]],
    timestamps: Sequence[datetime],
) -> list[tuple[int, str]]:
    """
    Detect price-vs-MA crossovers over aligned, same-length series.

    Returns `(index, direction)` pairs where `direction` is 'bullish'
    (price crossed above the MA) or 'bearish' (crossed below), evaluated
    at bar `index`.

    A crossing at `i` requires BOTH bar `i-1` and bar `i` to have a price
    and an MA value (None — warmup or gaps — breaks the comparison, no
    fire). Sign convention uses `diff = price - ma`, matching TradingView's
    `ta.crossover` / `ta.crossunder` (the de-facto charting standard):
        bullish: prev_diff <= 0 and cur_diff > 0
        bearish: prev_diff >= 0 and cur_diff < 0
    A bar sitting exactly on the MA (diff == 0) counts as "at or above", so
    a subsequent strict move below fires bearish — same as TradingView. The
    strict current sign (`> 0` / `< 0`) prevents firing on the touch bar
    itself, so a single cross yields a single event.
    """
    if not (len(price) == len(ma) == len(timestamps)):
        raise ValueError("price, ma, timestamps must be the same length")

    out: list[tuple[int, str]] = []
    for i in range(1, len(price)):
        p0, p1 = price[i - 1], price[i]
        m0, m1 = ma[i - 1], ma[i]
        if p0 is None or p1 is None or m0 is None or m1 is None:
            continue
        prev = p0 - m0
        cur = p1 - m1
        if prev <= 0 and cur > 0:
            out.append((i, "bullish"))
        elif prev >= 0 and cur < 0:
            out.append((i, "bearish"))
    return out


def scan_ma_crossovers(
    symbol: str,
    *,
    ma: str,
    start: datetime,
    end: datetime,
    display_agg: str = "1d",
    length: Optional[int] = None,
    source_agg: Optional[str] = None,
    window_days: Optional[int] = None,
    reader: Optional[IndicatorReader] = None,
) -> list[MACrossoverAlert]:
    """
    Compute the MA over `[start, end)` and return every price-vs-MA
    crossover, newest last.

    Cross-timeframe is the same contract as the chart: pass `source_agg`
    (bar-locked) or `window_days` (window-locked) to lock the MA to a
    coarser bar than `display_agg`. The MA — including its forward-fill
    onto the display bars and its warmup masking — comes straight from
    `IndicatorReader`, so detection runs on the exact numbers rendered.
    """
    ma = ma.lower()
    if ma not in _MA_NAMES:
        raise ValueError(f"ma must be one of {sorted(_MA_NAMES)}, got {ma!r}")

    reader = reader or IndicatorReader.from_settings()
    params: dict = {}
    if length is not None:
        params["period"] = length
    spec = {"name": ma, "params": params}
    if window_days is not None:
        spec["window_days"] = window_days
    elif source_agg is not None:
        spec["source_agg"] = source_agg

    chart = reader.get_chart_data(
        symbol, [spec], start=start, end=end, interval=display_agg,
    )
    if not chart.series or not chart.bars:
        return []
    series = chart.series[0]
    if not series.values:
        return []

    # bars and series.values are both display-aligned, same order/length.
    prices = [b.close for b in chart.bars]
    ma_vals = [v.value for v in series.values]
    timestamps = [b.timestamp for b in chart.bars]

    resolved_source = series.source_agg or display_agg
    resolved_length = int(series.params.get("period", length or 0))

    alerts: list[MACrossoverAlert] = []
    for i, direction in detect_crossings(prices, ma_vals, timestamps):
        alerts.append(MACrossoverAlert(
            symbol=symbol,
            direction=direction,
            ma=ma,  # type: ignore[arg-type]
            length=resolved_length,
            source_agg=resolved_source,
            display_agg=display_agg,
            crossed_at=timestamps[i],
            price=float(prices[i]),
            ma_value=float(ma_vals[i]),  # not None — detect_crossings guarantees it
        ))
    logger.info(
        "scan_ma_crossovers(%s %s%d src=%s disp=%s): %d crossings",
        symbol, ma, resolved_length, resolved_source, display_agg, len(alerts),
    )
    return alerts
