"""
Interval helpers shared by Context + Backtester for multi-timeframe support.

Convention: bar timestamps are at the START of the bar's window. A
daily bar timestamped 2024-08-01 00:00 UTC covers 2024-08-01
00:00:00 → 2024-08-01 23:59:59. Its "ready time" (when the bar is
fully formed and safe to consume without look-ahead) is its
timestamp + the interval's duration.

For multi-timeframe strategies, the iteration loop walks the
EXECUTION interval (finest). At each step, coarser-interval bars
are visible to the strategy only if their ready time has passed:

    coarser_bar visible iff  coarser_bar.timestamp + duration(coarser) <= execution_bar.timestamp

This is the no-look-ahead invariant. Tests in `test_multi_timeframe.py`
pin it.
"""
from __future__ import annotations

from datetime import timedelta

# Seconds per interval — used to compute bar "ready times" and to order
# intervals coarsest-to-finest. Add new intervals here only — the rest of
# the system reads from this map.
_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}


def interval_seconds(interval: str) -> int:
    """Return the duration of one bar at `interval` in seconds."""
    try:
        return _INTERVAL_SECONDS[interval]
    except KeyError as exc:
        supported = ", ".join(_INTERVAL_SECONDS)
        raise ValueError(
            f"Unknown interval {interval!r}. Supported: {supported}."
        ) from exc


def interval_duration(interval: str) -> timedelta:
    """Return `timedelta` for one bar at `interval`."""
    return timedelta(seconds=interval_seconds(interval))


def validate_intervals_order(intervals: list[str]) -> None:
    """
    Strategies declare intervals coarsest-to-finest. The execution
    interval is `intervals[-1]`. Enforce that ordering — otherwise
    consumers can't safely treat `intervals[-1]` as the finest.

    Coarsest-to-finest means durations are strictly DECREASING:
    `seconds(intervals[i]) > seconds(intervals[i+1])` for all i.
    """
    if not intervals:
        raise ValueError("intervals list must not be empty")
    for i in range(len(intervals) - 1):
        a, b = intervals[i], intervals[i + 1]
        a_s, b_s = interval_seconds(a), interval_seconds(b)
        if a_s == b_s:
            raise ValueError(f"intervals must be unique; got duplicate {a!r}.")
        if a_s < b_s:
            raise ValueError(
                f"intervals must be ordered coarsest-to-finest; "
                f"got {a!r} ({a_s}s) before {b!r} ({b_s}s)."
            )


def execution_interval(intervals: list[str]) -> str:
    """Last entry in `intervals` — the finest, what the backtester iterates on."""
    if not intervals:
        raise ValueError("intervals list must not be empty")
    return intervals[-1]


def supported_intervals() -> list[str]:
    """All registered intervals, coarsest-to-finest by duration."""
    return sorted(_INTERVAL_SECONDS, key=lambda i: -_INTERVAL_SECONDS[i])
