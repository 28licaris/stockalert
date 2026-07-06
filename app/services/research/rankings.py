"""Research rankings over the top-1000 liquid daily universe (`ohlcv_daily`).

Read-only, on-demand ClickHouse SQL. The universe holds 20yr of history
including reused/relisted tickers with multi-year gaps, so EVERY ranking is
**guarded**:
  - recent-window anchor (only bars in the last ~180 calendar days),
  - no-gap lookback (the "N days ago" price must itself be recent),
  - liquidity floor (min avg dollar-volume),
  - currently-trading (latest bar within a few days of the table max).

Without these a naïve N-back return returns garbage (e.g. NE +22,506% from a
6-year bankruptcy gap). See docs/research_page_spec.md.

Freshness: results are "as of the latest close in ohlcv_daily" — surfaced in
the response `as_of`. Keeping that table current is a separate ops concern.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

PRESETS = ("momentum", "gainers", "losers", "most_active", "streak_up", "streak_down")

# Trading-day lookback → calendar-day anchor (≈1.4× to clear weekends/holidays).
_LOOKBACK_CAL = {20: 30, 60: 84, 120: 170}
_WINDOW_CAL = 180  # how far back we read (bounds the ticker-reuse landmines out)


def _candidates_sql(lookback_cal: int, min_dollar_vol: float) -> str:
    """Per-symbol guarded metrics. Numeric args are server-controlled ints/floats."""
    gap_guard_cal = int(lookback_cal * 1.5)
    return f"""
    WITH (SELECT max(toDate(timestamp)) FROM ohlcv_daily) AS md
    SELECT
        symbol,
        last_close,
        chg_1d,
        ret_look,
        dvol,
        up_streak,
        down_streak,
        (c_look > 0 AND look_date >= md - {gap_guard_cal}) AS look_ok
    FROM (
        SELECT
            symbol,
            argMax(close, timestamp)                                   AS last_close,
            argMaxIf(close, timestamp, toDate(timestamp) <= md - 1)    AS c_1d,
            argMaxIf(close, timestamp, toDate(timestamp) <= md - {lookback_cal}) AS c_look,
            maxIf(toDate(timestamp), toDate(timestamp) <= md - {lookback_cal})   AS look_date,
            max(toDate(timestamp))                                     AS last_date,
            avg(close * volume)                                        AS dvol,
            last_close / nullIf(c_1d, 0) - 1                           AS chg_1d,
            last_close / nullIf(c_look, 0) - 1                         AS ret_look,
            arrayReverse(groupArray(sg))                               AS rev,
            if(arrayFirstIndex(x -> x != 1, rev) = 0,  toInt64(length(rev)), toInt64(arrayFirstIndex(x -> x != 1, rev)) - 1)  AS up_streak,
            if(arrayFirstIndex(x -> x != -1, rev) = 0, toInt64(length(rev)), toInt64(arrayFirstIndex(x -> x != -1, rev)) - 1) AS down_streak
        FROM (
            SELECT symbol, timestamp, close, volume,
                   sign(close - lagInFrame(close) OVER (PARTITION BY symbol ORDER BY timestamp)) AS sg
            FROM ohlcv_daily FINAL
            WHERE toDate(timestamp) >= md - {_WINDOW_CAL}
            ORDER BY symbol, timestamp
        )
        GROUP BY symbol
    )
    WHERE last_date >= md - 5 AND dvol >= {min_dollar_vol}
    """


# preset → (extra WHERE, ORDER BY)
_PRESET_SORT = {
    "momentum": ("look_ok", "ret_look DESC"),
    "gainers": ("isFinite(chg_1d)", "chg_1d DESC"),
    "losers": ("isFinite(chg_1d)", "chg_1d ASC"),
    "most_active": ("1", "dvol DESC"),
    "streak_up": ("up_streak >= {streak_min}", "up_streak DESC, ret_look DESC"),
    "streak_down": ("down_streak >= {streak_min}", "down_streak DESC"),
}


def rank(
    preset: str,
    *,
    lookback_days: int = 60,
    top_n: int = 50,
    min_dollar_vol: float = 10_000_000.0,
    streak_min: int = 3,
) -> dict[str, Any]:
    """Return `{as_of, preset, count, rows:[...]}` for a preset ranking."""
    if preset not in _PRESET_SORT:
        raise ValueError(f"unknown preset {preset!r}")
    lookback_days = lookback_days if lookback_days in _LOOKBACK_CAL else 60
    top_n = max(1, min(int(top_n), 200))
    streak_min = max(1, min(int(streak_min), 30))
    lookback_cal = _LOOKBACK_CAL[lookback_days]

    where_extra, order_by = _PRESET_SORT[preset]
    where_extra = where_extra.format(streak_min=streak_min)

    sql = f"""
    SELECT symbol, round(last_close, 2) AS price,
           round(chg_1d * 100, 2) AS chg_1d_pct,
           round(ret_look * 100, 2) AS ret_pct,
           round(dvol, 0) AS dollar_vol,
           toInt32(up_streak) AS up_streak,
           toInt32(down_streak) AS down_streak
    FROM ( {_candidates_sql(lookback_cal, min_dollar_vol)} )
    WHERE {where_extra}
    ORDER BY {order_by}
    LIMIT {top_n}
    """

    from app.db.client import get_client

    client = get_client()
    rows = client.query(sql).result_rows
    as_of = client.query("SELECT max(toDate(timestamp)) FROM ohlcv_daily").result_rows[0][0]

    symbols = [r[0] for r in rows]
    names = _names(symbols)
    out = [
        {
            "symbol": r[0],
            "name": names.get(r[0], ""),
            "price": r[1],
            "chg_1d_pct": r[2],
            "ret_pct": r[3],
            "dollar_vol": r[4],
            "up_streak": r[5],
            "down_streak": r[6],
        }
        for r in rows
    ]
    return {
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
        "preset": preset,
        "lookback_days": lookback_days,
        "count": len(out),
        "rows": out,
    }


def _names(symbols: list[str]) -> dict[str, str]:
    """Company names from the durable instrument-names cache (CH-only read)."""
    if not symbols:
        return {}
    try:
        from app.services.instruments.names import resolve_names

        return {s: (v.get("description") or "") for s, v in resolve_names(symbols).items()}
    except Exception as exc:  # noqa: BLE001 — names are best-effort
        logger.warning("research rankings name lookup failed: %s", exc)
        return {}
