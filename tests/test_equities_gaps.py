"""Unit tests for `app/services/equities/gaps.py` (CV3).

Verifies the gap-detection helpers used by the CV3 history-backfill
script and (post-Phase 1B) by the v2 nightly cron. Same algorithm as
the v1 bronze gaps module so we lean on the same test shapes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pyarrow as pa
import pytest

pyiceberg = pytest.importorskip("pyiceberg")

from app.services.equities.gaps import (  # noqa: E402
    latest_loaded_date,
    loaded_dates_in_range,
    missing_weekdays,
    yesterday_et,
)


def _table_with_timestamps(timestamps: list[datetime]) -> MagicMock:
    """Build a mock PyIceberg `Table` whose scan returns the given
    timestamps. Mocks BOTH ``to_arrow()`` (used by latest_loaded_date)
    and ``to_arrow_batch_reader()`` (used by loaded_dates_in_range, which
    streams batches to stay OOM-safe on whole-market scans)."""
    table = MagicMock()
    table.name.return_value = "lake.equities.polygon_raw"
    arrow = pa.table({"timestamp": pa.array(timestamps, type=pa.timestamp("us", tz="UTC"))})
    scan = MagicMock()
    scan.to_arrow.return_value = arrow
    scan.to_arrow_batch_reader.return_value = arrow.to_batches()
    table.scan.return_value = scan
    return table


def _empty_table() -> MagicMock:
    table = MagicMock()
    table.name.return_value = "lake.equities.polygon_raw"
    arrow = pa.table({"timestamp": pa.array([], type=pa.timestamp("us", tz="UTC"))})
    scan = MagicMock()
    scan.to_arrow.return_value = arrow
    scan.to_arrow_batch_reader.return_value = arrow.to_batches()
    table.scan.return_value = scan
    return table


# ─────────────────────────────────────────────────────────────────────
# latest_loaded_date
# ─────────────────────────────────────────────────────────────────────

def test_latest_loaded_date_returns_et_date_not_utc_date():
    """A bar at 2024-05-15 00:30 UTC = 2024-05-14 20:30 ET (after-hours
    of trading day May 14). Must return May 14, not May 15."""
    ts = datetime(2024, 5, 15, 0, 30, tzinfo=timezone.utc)
    table = _table_with_timestamps([ts])

    assert latest_loaded_date(table) == date(2024, 5, 14)


def test_latest_loaded_date_returns_max_when_multiple():
    table = _table_with_timestamps([
        datetime(2024, 5, 13, 18, 0, tzinfo=timezone.utc),
        datetime(2024, 5, 14, 18, 0, tzinfo=timezone.utc),
        datetime(2024, 5, 12, 18, 0, tzinfo=timezone.utc),
    ])
    assert latest_loaded_date(table) == date(2024, 5, 14)


def test_latest_loaded_date_empty_table_returns_none():
    assert latest_loaded_date(_empty_table()) is None


def test_latest_loaded_date_scan_failure_raises():
    """NO SILENT FAILURES: a scan error must propagate, NOT degrade to
    None. None means 'genuinely empty'; an error means 'couldn't read'
    — conflating them let an ACCESS_DENIED masquerade as cold-start."""
    table = MagicMock()
    table.name.return_value = "lake.equities.polygon_raw"
    table.scan.side_effect = RuntimeError("S3 timeout")
    with pytest.raises(RuntimeError, match="S3 timeout"):
        latest_loaded_date(table)


def test_missing_weekdays_skips_run_on_scan_failure():
    """A coverage-scan failure must NOT be read as cold-start (which
    would blind-refetch max_lookback_days every run). missing_weekdays
    returns [] (skip this run) so nothing is backfilled until the read
    error is fixed."""
    table = MagicMock()
    table.name.return_value = "lake.equities.schwab_universe"
    table.scan.side_effect = RuntimeError("AWS Error ACCESS_DENIED")
    assert missing_weekdays(table) == []


# ─────────────────────────────────────────────────────────────────────
# missing_weekdays
# ─────────────────────────────────────────────────────────────────────

def test_missing_weekdays_fills_gap_after_latest():
    """latest=Mon May 13 → missing should be Tue May 14, Wed May 15
    (assuming through=Wed May 15)."""
    table = _table_with_timestamps([
        datetime(2024, 5, 13, 18, 0, tzinfo=timezone.utc),
    ])

    result = missing_weekdays(table, through=date(2024, 5, 15))

    assert result == [date(2024, 5, 14), date(2024, 5, 15)]


def test_missing_weekdays_skips_weekends():
    table = _table_with_timestamps([
        datetime(2024, 5, 9, 18, 0, tzinfo=timezone.utc),  # Thu
    ])
    # Window Thu→Mon should produce only Fri + Mon, skipping Sat+Sun.
    result = missing_weekdays(table, through=date(2024, 5, 13))

    assert result == [date(2024, 5, 10), date(2024, 5, 13)]


def test_missing_weekdays_returns_empty_when_table_is_current():
    table = _table_with_timestamps([
        datetime(2024, 5, 15, 18, 0, tzinfo=timezone.utc),
    ])
    result = missing_weekdays(table, through=date(2024, 5, 15))
    assert result == []


def test_missing_weekdays_cold_start_when_empty():
    """No data in lookback → return `max_lookback_days` of weekdays
    counting back from `through` (cold-start fallback)."""
    table = _empty_table()
    result = missing_weekdays(
        table, through=date(2024, 5, 15), max_lookback_days=5,
    )

    # Window is May 10 (Fri).. May 15 (Wed) = Fri, Mon, Tue, Wed (skip weekend)
    assert result == [
        date(2024, 5, 10),
        date(2024, 5, 13),
        date(2024, 5, 14),
        date(2024, 5, 15),
    ]


# ─────────────────────────────────────────────────────────────────────
# loaded_dates_in_range
# ─────────────────────────────────────────────────────────────────────

def test_loaded_dates_in_range_returns_distinct_et_dates_within_window():
    table = _table_with_timestamps([
        datetime(2024, 5, 13, 14, 30, tzinfo=timezone.utc),  # in window
        datetime(2024, 5, 14, 14, 30, tzinfo=timezone.utc),  # in window
        datetime(2024, 5, 14, 18, 30, tzinfo=timezone.utc),  # dup of above
        datetime(2024, 5, 20, 14, 30, tzinfo=timezone.utc),  # outside window
    ])
    result = loaded_dates_in_range(
        table, start=date(2024, 5, 13), end=date(2024, 5, 15),
    )
    assert result == {date(2024, 5, 13), date(2024, 5, 14)}


def test_loaded_dates_in_range_after_hours_maps_to_correct_trading_day():
    """A 2024-05-15 00:30 UTC bar = 2024-05-14 20:30 ET, which belongs
    to trading day May 14, not May 15."""
    table = _table_with_timestamps([
        datetime(2024, 5, 15, 0, 30, tzinfo=timezone.utc),
    ])
    result = loaded_dates_in_range(
        table, start=date(2024, 5, 14), end=date(2024, 5, 14),
    )
    assert result == {date(2024, 5, 14)}


def test_loaded_dates_in_range_returns_empty_on_scan_failure():
    """A scan failure must surface as 'skip nothing' rather than
    silently masking an Iceberg error. Caller sees the empty set and
    proceeds with the full window."""
    table = MagicMock()
    table.name.return_value = "lake.equities.polygon_raw"
    table.scan.side_effect = RuntimeError("auth denied")
    result = loaded_dates_in_range(
        table, start=date(2024, 5, 1), end=date(2024, 5, 31),
    )
    assert result == set()


def test_loaded_dates_in_range_empty_table_returns_empty_set():
    result = loaded_dates_in_range(
        _empty_table(), start=date(2024, 5, 1), end=date(2024, 5, 31),
    )
    assert result == set()


# ─────────────────────────────────────────────────────────────────────
# yesterday_et — basic shape test (ET is anchored, can't test exact value)
# ─────────────────────────────────────────────────────────────────────

def test_yesterday_et_is_a_recent_date():
    today = date.today()
    y = yesterday_et()
    assert (today - timedelta(days=2)) <= y <= today
