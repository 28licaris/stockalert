"""
Tests for the corp-action rebuild trigger (TA-5.1.9).

When a new split lands in silver.corp_actions for symbol S with
ex_date X, every silver.ohlcv_1m row for S with bar_date < X has a
stale F (cumulative split factor) baked in. The dirty-scan + rebuild
logic in `SilverOhlcvBuild` catches this and recomputes the affected
windows on the next nightly run.

These tests verify:
  - find_corp_action_dirty_symbols returns the right per-symbol max ex_date
  - Empty corp_actions / no new splits → empty result
  - Missing silver.corp_actions table → graceful empty
  - run_nightly with scan_corp_action_dirty=True does dirty work + yesterday
  - run_nightly with scan_corp_action_dirty=False skips the scan
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from app.services.silver.ohlcv.build import (
    BuildResult,
    SilverOhlcvBuild,
    SliceResult,
)


# ─────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────


class _FakeScan:
    def __init__(self, arrow: pa.Table) -> None:
        self._arrow = arrow

    def to_arrow(self) -> pa.Table:
        return self._arrow


class _FakeCorpActionsTable:
    """Stand-in for silver.corp_actions Iceberg table."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.scan_calls: list[Any] = []

    def scan(self, *, row_filter: Any = None, selected_fields: Any = None, **_kw):
        self.scan_calls.append(row_filter)
        if not self._rows:
            schema = pa.schema([
                pa.field("symbol", pa.string()),
                pa.field("ex_date", pa.date32()),
                pa.field("ingestion_ts", pa.timestamp("us", tz="UTC")),
            ])
            return _FakeScan(pa.Table.from_pylist([], schema=schema))
        return _FakeScan(pa.Table.from_pylist(self._rows))


class _FakeCatalog:
    def __init__(self, ca_table: _FakeCorpActionsTable | None = None) -> None:
        self._ca_table = ca_table
        self.load_calls: list[Any] = []

    def load_table(self, identifier: Any):
        self.load_calls.append(identifier)
        if self._ca_table is None:
            from pyiceberg.exceptions import NoSuchTableError
            raise NoSuchTableError(f"missing: {identifier}")
        return self._ca_table


def _split_row(symbol: str, ex_date: date, ingestion_ts: datetime) -> dict:
    return {
        "symbol": symbol,
        "ex_date": ex_date,
        "action_type": "split",
        "ingestion_ts": ingestion_ts,
    }


# ─────────────────────────────────────────────────────────────────────
# find_corp_action_dirty_symbols
# ─────────────────────────────────────────────────────────────────────


class TestFindCorpActionDirtySymbols:
    def test_no_corp_actions_table_returns_empty(self) -> None:
        build = SilverOhlcvBuild(
            catalog=_FakeCatalog(ca_table=None),
            ohlcv_table=object(), bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )
        dirty = build.find_corp_action_dirty_symbols(
            since=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        assert dirty == {}

    def test_empty_corp_actions_returns_empty(self) -> None:
        build = SilverOhlcvBuild(
            catalog=_FakeCatalog(ca_table=_FakeCorpActionsTable([])),
            ohlcv_table=object(), bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )
        dirty = build.find_corp_action_dirty_symbols(
            since=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        assert dirty == {}

    def test_single_new_split_flags_symbol(self) -> None:
        ts = datetime(2024, 6, 5, tzinfo=timezone.utc)
        rows = [_split_row("NVDA", date(2024, 6, 10), ts)]
        build = SilverOhlcvBuild(
            catalog=_FakeCatalog(ca_table=_FakeCorpActionsTable(rows)),
            ohlcv_table=object(), bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )
        dirty = build.find_corp_action_dirty_symbols(
            since=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        assert dirty == {"NVDA": date(2024, 6, 10)}

    def test_multiple_splits_same_symbol_keeps_max_ex_date(self) -> None:
        """When a symbol has multiple new splits, rebuild window must
        cover all of them — so we keep the LATEST ex_date as the cap."""
        ts = datetime(2024, 6, 5, tzinfo=timezone.utc)
        rows = [
            _split_row("AAPL", date(2020, 8, 31), ts),  # earlier
            _split_row("AAPL", date(2024, 1, 15), ts),  # latest
            _split_row("AAPL", date(2022, 3, 1), ts),  # middle
        ]
        build = SilverOhlcvBuild(
            catalog=_FakeCatalog(ca_table=_FakeCorpActionsTable(rows)),
            ohlcv_table=object(), bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )
        dirty = build.find_corp_action_dirty_symbols(
            since=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        assert dirty == {"AAPL": date(2024, 1, 15)}

    def test_multiple_symbols_each_get_their_own_max(self) -> None:
        ts = datetime(2024, 6, 5, tzinfo=timezone.utc)
        rows = [
            _split_row("NVDA", date(2024, 6, 10), ts),
            _split_row("AAPL", date(2020, 8, 31), ts),
            _split_row("TSLA", date(2022, 8, 25), ts),
        ]
        build = SilverOhlcvBuild(
            catalog=_FakeCatalog(ca_table=_FakeCorpActionsTable(rows)),
            ohlcv_table=object(), bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )
        dirty = build.find_corp_action_dirty_symbols(
            since=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        assert dirty == {
            "NVDA": date(2024, 6, 10),
            "AAPL": date(2020, 8, 31),
            "TSLA": date(2022, 8, 25),
        }

    def test_filter_pushes_action_type_and_since(self) -> None:
        """The scan must filter on action_type='split' AND
        ingestion_ts > since — otherwise we'd flag dividends as needing
        rebuild (they don't change F)."""
        ca_table = _FakeCorpActionsTable([])
        build = SilverOhlcvBuild(
            catalog=_FakeCatalog(ca_table=ca_table),
            ohlcv_table=object(), bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )
        build.find_corp_action_dirty_symbols(
            since=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        assert len(ca_table.scan_calls) == 1
        # The filter is an And() expression; structurally check it.
        rf = ca_table.scan_calls[0]
        rf_str = str(rf)
        assert "action_type" in rf_str
        assert "split" in rf_str
        assert "ingestion_ts" in rf_str

    def test_scan_failure_returns_empty_no_raise(self) -> None:
        """If the corp_actions scan raises, we degrade gracefully —
        nightly should still run yesterday's window."""
        bad_table = MagicMock()
        bad_table.scan.side_effect = RuntimeError("snapshot expired")
        build = SilverOhlcvBuild(
            catalog=_FakeCatalog(ca_table=bad_table),
            ohlcv_table=object(), bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )
        dirty = build.find_corp_action_dirty_symbols(
            since=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        assert dirty == {}


# ─────────────────────────────────────────────────────────────────────
# run_nightly with scan_corp_action_dirty
# ─────────────────────────────────────────────────────────────────────


class TestRunNightlyWithDirtyScan:
    def test_skip_dirty_scan_when_flag_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        build = SilverOhlcvBuild(
            catalog=object(), ohlcv_table=object(),
            bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )

        # Spy on the dirty rebuild — should NOT be called.
        called = {"dirty": False}

        def _spy_dirty():
            called["dirty"] = True
            return None

        monkeypatch.setattr(
            build, "_run_corp_action_dirty_rebuilds", _spy_dirty,
        )

        # Stub build_window so we don't touch real Iceberg.
        def _spy_window(symbols, start, end):
            t = datetime.now(timezone.utc)
            return BuildResult(
                run_id="x", started_at=t, finished_at=t,
                symbols=list(symbols), start_date=start, end_date=end,
            )

        monkeypatch.setattr(build, "build_window", _spy_window)

        build.run_nightly(["AAPL"], scan_corp_action_dirty=False)
        assert called["dirty"] is False

    def test_dirty_scan_runs_and_merges_results(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When dirty rebuilds + yesterday both have slices, the
        combined BuildResult includes both."""
        build = SilverOhlcvBuild(
            catalog=object(), ohlcv_table=object(),
            bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )

        # Dirty path returns 2 slices (e.g. 1 symbol × 2 days).
        t0 = datetime(2024, 6, 10, tzinfo=timezone.utc)
        t1 = datetime(2024, 6, 11, tzinfo=timezone.utc)
        dirty_result = BuildResult(
            run_id="dirty-id",
            started_at=t0, finished_at=t0,
            symbols=["NVDA"],
            start_date=date(2024, 6, 5), end_date=date(2024, 6, 9),
            slices=[
                SliceResult(symbol="NVDA", date=date(2024, 6, 5), silver_rows_written=100),
                SliceResult(symbol="NVDA", date=date(2024, 6, 6), silver_rows_written=100),
            ],
        )
        monkeypatch.setattr(
            build, "_run_corp_action_dirty_rebuilds", lambda: dirty_result,
        )

        # Yesterday path returns 1 slice.
        yesterday_result = BuildResult(
            run_id="yest-id",
            started_at=t1, finished_at=t1,
            symbols=["AAPL"],
            start_date=date(2024, 6, 11), end_date=date(2024, 6, 11),
            slices=[
                SliceResult(symbol="AAPL", date=date(2024, 6, 11), silver_rows_written=200),
            ],
        )
        monkeypatch.setattr(
            build, "build_window", lambda s, st, e: yesterday_result,
        )

        combined = build.run_nightly(["AAPL"], scan_corp_action_dirty=True)
        # Should be the dirty result with yesterday's slices appended.
        assert combined.run_id == "dirty-id"
        assert len(combined.slices) == 3
        # Both phases represented.
        symbols_processed = {s.symbol for s in combined.slices}
        assert symbols_processed == {"NVDA", "AAPL"}

    def test_no_dirty_falls_through_to_yesterday_only(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        build = SilverOhlcvBuild(
            catalog=object(), ohlcv_table=object(),
            bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )
        # Dirty returns None (no dirty symbols).
        monkeypatch.setattr(
            build, "_run_corp_action_dirty_rebuilds", lambda: None,
        )
        # Yesterday returns a normal result.
        called = {"build_window": False}

        def _spy_window(symbols, start, end):
            called["build_window"] = True
            t = datetime.now(timezone.utc)
            return BuildResult(
                run_id="yest", started_at=t, finished_at=t,
                symbols=list(symbols), start_date=start, end_date=end,
            )

        monkeypatch.setattr(build, "build_window", _spy_window)

        result = build.run_nightly(["AAPL"], scan_corp_action_dirty=True)
        assert called["build_window"] is True
        assert result.run_id == "yest"


# ─────────────────────────────────────────────────────────────────────
# Rebuild window math
# ─────────────────────────────────────────────────────────────────────


class TestRebuildWindowMath:
    def test_rebuild_excludes_ex_date_itself(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The rebuild window must be (bronze_start, max_ex_date - 1).
        The split day itself + everything after already has F=correct
        (their build saw the new split)."""
        ts = datetime(2024, 6, 5, tzinfo=timezone.utc)
        rows = [_split_row("NVDA", date(2024, 6, 10), ts)]
        build = SilverOhlcvBuild(
            catalog=_FakeCatalog(ca_table=_FakeCorpActionsTable(rows)),
            ohlcv_table=object(), bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )

        # Pre-prime cache so the rebuild doesn't try to load corp_actions.
        build._split_index = {}
        build._corp_actions_arrow = pa.table({"symbol": []})

        # Stub the watermark + the slice build + the run recorder.
        monkeypatch.setattr(
            build, "_get_last_run_started_at",
            lambda: datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        captured: list[tuple[str, date]] = []

        def _spy_slice(symbol, day, *, run_id=None):
            captured.append((symbol, day))
            return SliceResult(symbol=symbol, date=day, silver_rows_written=1)

        monkeypatch.setattr(build, "build_slice", _spy_slice)
        monkeypatch.setattr(build, "_record_run", lambda r: None)
        # Suppress the cache re-prime in the rebuild path.
        monkeypatch.setattr(build, "_prime_corp_actions_cache", lambda: None)

        result = build._run_corp_action_dirty_rebuilds()
        assert result is not None

        # Every captured day must be < the ex_date.
        ex_date = date(2024, 6, 10)
        for symbol, day in captured:
            assert symbol == "NVDA"
            assert day < ex_date

        # And the rebuild starts at BRONZE_HISTORY_START (2021-01-04).
        days = sorted({d for _, d in captured})
        assert days[0] == date(2021, 1, 4)
        assert days[-1] == ex_date - timedelta(days=1)
