"""
Unit tests for the LiveLakeWriter (TA-5.7).

Tests are network-free — we exercise the type contracts, the
validation guards, and the row → Arrow conversion using stubbed
dependencies. Real CH + Iceberg writes happen in the integration
runbook (TA-5.7.6 live verification), not in pytest.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from app.services.ingest.live_lake_writer import (
    CycleResult,
    LiveLakeWriter,
    _EQUITIES_SCHWAB_ARROW,
    _ProviderConfig,
)


# ─────────────────────────────────────────────────────────────────────
# Construction + validation
# ─────────────────────────────────────────────────────────────────────


class TestConstruction:
    def test_default_construction_via_from_settings(self) -> None:
        w = LiveLakeWriter.from_settings()
        # Defaults from config (5, 15)
        assert w._cycle_minutes == 5
        assert w._lookback_minutes == 15
        assert "schwab" in w._provider_config

    def test_explicit_construction(self) -> None:
        w = LiveLakeWriter(cycle_minutes=10, lookback_minutes=30)
        assert w._cycle_minutes == 10
        assert w._lookback_minutes == 30

    def test_zero_cycle_raises(self) -> None:
        with pytest.raises(ValueError, match="cycle_minutes must be > 0"):
            LiveLakeWriter(cycle_minutes=0)

    def test_negative_cycle_raises(self) -> None:
        with pytest.raises(ValueError):
            LiveLakeWriter(cycle_minutes=-1)

    def test_lookback_less_than_cycle_raises(self) -> None:
        """Lookback < cycle means gaps possible. Refuse."""
        with pytest.raises(ValueError, match="lookback_minutes must be >="):
            LiveLakeWriter(cycle_minutes=10, lookback_minutes=5)

    def test_lookback_equal_to_cycle_ok(self) -> None:
        """Edge: lookback==cycle is allowed (zero overlap; still safe)."""
        w = LiveLakeWriter(cycle_minutes=5, lookback_minutes=5)
        assert w._lookback_minutes == 5


# ─────────────────────────────────────────────────────────────────────
# Provider config — pluggable contract
# ─────────────────────────────────────────────────────────────────────


class TestProviderConfig:
    def test_default_provider_config_has_schwab(self) -> None:
        w = LiveLakeWriter()
        cfg = w._provider_config["schwab"]
        assert cfg.live_source_tag == "schwab-stream"
        assert cfg.equities_table_name == "schwab_universe"

    def test_custom_provider_config_accepted(self) -> None:
        """The class is provider-agnostic — you can pass any config."""
        custom = {
            "alpaca": _ProviderConfig(
                live_source_tag="alpaca-stream",
                equities_table_name="alpaca_universe",
            ),
        }
        w = LiveLakeWriter(provider_config=custom)
        assert "schwab" not in w._provider_config
        assert w._provider_config["alpaca"].live_source_tag == "alpaca-stream"


# ─────────────────────────────────────────────────────────────────────
# Row → Arrow conversion
# ─────────────────────────────────────────────────────────────────────


class TestRowsToArrow:
    def _row(self, **overrides) -> dict:
        """Default row, override fields."""
        base = {
            "symbol": "AAPL",
            "timestamp": datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc),
            "open": 200.0,
            "high": 201.0,
            "low": 199.0,
            "close": 200.5,
            "volume": 100000.0,
            "vwap": 200.25,
            "trade_count": 1234,
            "source": "schwab-stream",
        }
        base.update(overrides)
        return base

    def test_schema_matches_bronze(self) -> None:
        arrow = LiveLakeWriter._rows_to_arrow([self._row()], run_id="r1")
        assert arrow.schema.equals(_EQUITIES_SCHWAB_ARROW)

    def test_audit_metadata_stamped(self) -> None:
        arrow = LiveLakeWriter._rows_to_arrow([self._row()], run_id="run-test-1")
        assert arrow["ingestion_run_id"][0].as_py() == "run-test-1"
        assert arrow["ingestion_ts"][0].as_py() is not None

    def test_vwap_zero_normalized_to_null(self) -> None:
        """CH sometimes stores 0 for missing vwap; bronze convention is NULL."""
        arrow = LiveLakeWriter._rows_to_arrow([self._row(vwap=0)], run_id="r1")
        assert arrow["vwap"][0].as_py() is None

    def test_trade_count_zero_normalized_to_null(self) -> None:
        """Same for trade_count."""
        arrow = LiveLakeWriter._rows_to_arrow([self._row(trade_count=0)], run_id="r1")
        assert arrow["trade_count"][0].as_py() is None

    def test_naive_timestamp_coerced_to_utc(self) -> None:
        naive = datetime(2026, 5, 17, 14, 30)  # no tzinfo
        arrow = LiveLakeWriter._rows_to_arrow(
            [self._row(timestamp=naive)], run_id="r1",
        )
        ts = arrow["timestamp"][0].as_py()
        assert ts.tzinfo is not None

    def test_empty_rows_produces_empty_arrow(self) -> None:
        arrow = LiveLakeWriter._rows_to_arrow([], run_id="r1")
        assert arrow.num_rows == 0
        assert arrow.schema.equals(_EQUITIES_SCHWAB_ARROW)


# ─────────────────────────────────────────────────────────────────────
# Cycle-result shape
# ─────────────────────────────────────────────────────────────────────


class TestCycleResult:
    def test_total_rows_sums_per_provider(self) -> None:
        r = CycleResult(
            run_id="r1",
            started_at=datetime(2026, 5, 17, 14, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 5, 17, 14, 0, 2, tzinfo=timezone.utc),
            window_start=datetime(2026, 5, 17, 13, 44, tzinfo=timezone.utc),
            window_end=datetime(2026, 5, 17, 13, 59, tzinfo=timezone.utc),
            per_provider_rows_written={"schwab": 100, "polygon": 50},
        )
        assert r.total_rows == 150

    def test_succeeded_false_on_any_error(self) -> None:
        r = CycleResult(
            run_id="r1",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            window_start=datetime.now(timezone.utc),
            window_end=datetime.now(timezone.utc),
            per_provider_rows_written={"schwab": 100},
            per_provider_errors={"polygon": "boom"},
        )
        assert r.succeeded is False

    def test_succeeded_true_with_no_errors(self) -> None:
        r = CycleResult(
            run_id="r1",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            window_start=datetime.now(timezone.utc),
            window_end=datetime.now(timezone.utc),
            per_provider_rows_written={"schwab": 100},
        )
        assert r.succeeded is True

    def test_duration_seconds_computed(self) -> None:
        start = datetime(2026, 5, 17, 14, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 5, 17, 14, 0, 5, tzinfo=timezone.utc)
        r = CycleResult(
            run_id="r1", started_at=start, finished_at=end,
            window_start=start, window_end=start,
        )
        assert r.duration_seconds == 5.0


# ─────────────────────────────────────────────────────────────────────
# run_cycle — async tests with stubbed CH + Iceberg
# ─────────────────────────────────────────────────────────────────────


class TestRunCycle:
    @pytest.mark.asyncio
    async def test_empty_ch_window_produces_zero_rows(self) -> None:
        """When CH has no rows in the window, the cycle completes
        cleanly with per_provider_rows_written={'schwab': 0}."""
        w = LiveLakeWriter()
        with patch.object(LiveLakeWriter, "_read_ch", return_value=[]):
            result = await w.run_cycle(
                as_of=datetime(2026, 5, 17, 14, 0, tzinfo=timezone.utc),
            )
        assert result.per_provider_rows_written == {"schwab": 0}
        assert result.per_provider_errors == {}
        assert result.succeeded

    @pytest.mark.asyncio
    async def test_per_provider_error_isolated(self) -> None:
        """When one provider's read fails, the other providers still run.

        Cycle returns partial-fail status with the failing provider in
        `per_provider_errors`.
        """
        custom = {
            "good": _ProviderConfig("good-stream", "good_minute"),
            "bad": _ProviderConfig("bad-stream", "bad_minute"),
        }
        w = LiveLakeWriter(provider_config=custom)

        def stub_read(tag, *args, **kwargs):
            if tag == "bad-stream":
                raise RuntimeError("CH unreachable")
            return []  # good-stream returns empty

        with patch.object(LiveLakeWriter, "_read_ch", side_effect=stub_read):
            result = await w.run_cycle(
                as_of=datetime(2026, 5, 17, 14, 0, tzinfo=timezone.utc),
            )
        assert result.per_provider_rows_written.get("good") == 0
        assert "bad" in result.per_provider_errors
        assert not result.succeeded

    @pytest.mark.asyncio
    async def test_window_cutoff_one_minute_before_as_of(self) -> None:
        """`as_of - 1 min` is window_end; we don't read the in-flight
        minute (the batcher might still be writing it)."""
        w = LiveLakeWriter(cycle_minutes=5, lookback_minutes=15)
        captured = {}

        def stub_read(tag, start, end):
            captured["start"] = start
            captured["end"] = end
            return []

        as_of = datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc)
        with patch.object(LiveLakeWriter, "_read_ch", side_effect=stub_read):
            await w.run_cycle(as_of=as_of)

        assert captured["end"] == as_of - timedelta(minutes=1)
        # 1-min cutoff + 15-min lookback = window 16-min wide ending at as_of-1min
        assert captured["start"] == as_of - timedelta(minutes=16)


# ─────────────────────────────────────────────────────────────────────
# Audit: RTH detection (used by live_freshness check)
# ─────────────────────────────────────────────────────────────────────


# CV14: TestRthDetection removed. The `_is_rth` helper lived in
# app/services/bronze/audit/live_freshness.py (deleted with bronze/).
# No production code referenced it; the test was orphaned coverage.


# ─────────────────────────────────────────────────────────────────────
# Lifespan helpers (singleton + task management)
# ─────────────────────────────────────────────────────────────────────


class TestLifespanHelpers:
    def test_get_live_lake_writer_singleton(self) -> None:
        """get_live_lake_writer returns the same instance on repeat calls."""
        from app.services.ingest.live_lake_writer import (
            get_live_lake_writer, stop_live_lake_writer,
        )
        import asyncio

        # Reset any prior singleton state
        asyncio.run(stop_live_lake_writer())

        w1 = get_live_lake_writer()
        w2 = get_live_lake_writer()
        assert w1 is w2

        # Cleanup
        asyncio.run(stop_live_lake_writer())


# ─────────────────────────────────────────────────────────────────────
# Write path (2026-08-06). Until today EVERY test here stubbed _read_ch
# to return [], so the write path had NO coverage — which is how two
# production defects shipped: (1) the upsert fell back to copy-on-write
# and took ~6 min per 400-row chunk on a 5-min cycle, (2) it ran inline
# in the coroutine and froze the whole API. See ISSUES:
# live-lake-writer-blocks-event-loop.
# ─────────────────────────────────────────────────────────────────────


def _live_row(ts: datetime, symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol, "timestamp": ts, "open": 200.0, "high": 201.0,
        "low": 199.0, "close": 200.5, "volume": 100000.0, "vwap": 200.25,
        "trade_count": 1234, "source": "schwab-stream",
    }


class TestWritePath:
    @pytest.mark.asyncio
    async def test_appends_rows_and_does_not_upsert(self) -> None:
        """Must APPEND (O(rows)), never upsert (copy-on-write, O(table))."""
        w = LiveLakeWriter(cycle_minutes=5, lookback_minutes=15)
        as_of = datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc)
        rows = [_live_row(as_of - timedelta(minutes=3))]
        table = MagicMock()

        with patch.object(LiveLakeWriter, "_read_ch", return_value=rows), \
             patch("app.services.equities.tables.ensure_equities_table",
                   return_value=table), \
             patch("app.services.iceberg_catalog.get_catalog", MagicMock()):
            result = await w.run_cycle(as_of=as_of)

        assert result.per_provider_rows_written["schwab"] == 1
        assert table.append.call_count == 1
        assert table.upsert.call_count == 0, "upsert is copy-on-write; must not be used"

    @pytest.mark.asyncio
    async def test_watermark_suppresses_overlap_duplicates(self) -> None:
        """The 15-min read window overlaps the 5-min cycle on purpose. With
        append-only writes, only bars newer than the last write may land."""
        w = LiveLakeWriter(cycle_minutes=5, lookback_minutes=15)
        t0 = datetime(2026, 5, 17, 14, 0, tzinfo=timezone.utc)
        table = MagicMock()

        # cycle 1 sees bars at t0 and t0+1; cycle 2 re-reads both (overlap)
        # plus a new bar at t0+6 — only the new one may be appended.
        reads = [
            [_live_row(t0), _live_row(t0 + timedelta(minutes=1))],
            [_live_row(t0), _live_row(t0 + timedelta(minutes=1)),
             _live_row(t0 + timedelta(minutes=6))],
        ]

        with patch.object(LiveLakeWriter, "_read_ch", side_effect=reads), \
             patch("app.services.equities.tables.ensure_equities_table",
                   return_value=table), \
             patch("app.services.iceberg_catalog.get_catalog", MagicMock()):
            r1 = await w.run_cycle(as_of=t0 + timedelta(minutes=5))
            r2 = await w.run_cycle(as_of=t0 + timedelta(minutes=10))

        assert r1.per_provider_rows_written["schwab"] == 2
        assert r2.per_provider_rows_written["schwab"] == 1, "overlap re-appended"
        assert table.append.call_count == 2

    @pytest.mark.asyncio
    async def test_write_does_not_block_the_event_loop(self) -> None:
        """A slow lake write must degrade to 'the lake lags', never
        'the API is down' — the failure that took every page out."""
        import asyncio
        import time

        w = LiveLakeWriter(cycle_minutes=5, lookback_minutes=15)
        as_of = datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc)
        rows = [_live_row(as_of - timedelta(minutes=3))]
        table = MagicMock()
        table.append.side_effect = lambda *_a, **_k: time.sleep(0.30)

        ticks = 0

        async def _heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        with patch.object(LiveLakeWriter, "_read_ch", return_value=rows), \
             patch("app.services.equities.tables.ensure_equities_table",
                   return_value=table), \
             patch("app.services.iceberg_catalog.get_catalog", MagicMock()):
            beat = asyncio.create_task(_heartbeat())
            await w.run_cycle(as_of=as_of)
            beat.cancel()

        assert ticks > 5, f"event loop was blocked during the write (ticks={ticks})"
