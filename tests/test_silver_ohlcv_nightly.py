"""
Tests for the nightly silver-OHLCV build loop (TA-5.1.6).

Covers:
  - Gating logic (config-driven skip)
  - Symbol resolution ("seed" → SEED_SYMBOLS; CSV → list)
  - _seconds_until_next_run scheduling math
  - run_silver_ohlcv_build_nightly summary shape
  - Loop start when disabled (no-op return)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from app.services.silver.ohlcv import nightly as nightly_mod
from app.services.silver.ohlcv.build import BuildResult, SliceResult


class TestSecondsUntilNextRun:
    def test_target_later_today(self) -> None:
        # now = 06:00 UTC, run hour = 23:00 → ~17 hours until target.
        now = datetime(2024, 6, 10, 6, 0, 0, tzinfo=timezone.utc)
        secs = nightly_mod._seconds_until_next_run(23, now=now)
        assert 16 * 3600 < secs <= 17 * 3600

    def test_target_already_passed_today_uses_tomorrow(self) -> None:
        # now = 23:30 UTC, run hour = 23 → must use tomorrow 23:00.
        now = datetime(2024, 6, 10, 23, 30, 0, tzinfo=timezone.utc)
        secs = nightly_mod._seconds_until_next_run(23, now=now)
        # Tomorrow 23:00 minus today 23:30 = 23h 30m = 84600 sec.
        assert 23 * 3600 < secs <= 24 * 3600

    def test_clamps_invalid_hour(self) -> None:
        now = datetime(2024, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
        # 99 clamps to 23.
        secs_99 = nightly_mod._seconds_until_next_run(99, now=now)
        secs_23 = nightly_mod._seconds_until_next_run(23, now=now)
        assert secs_99 == secs_23


class TestResolveSymbols:
    def test_seed_keyword_returns_legacy_seed(self) -> None:
        """`seed` spec routes to the static SEED_SYMBOLS list for legacy
        operator scripts. Empty/None route to `active` (stream_universe)."""
        from app.data.seed_universe import SEED_SYMBOLS

        assert set(nightly_mod._resolve_symbols("seed")) == set(SEED_SYMBOLS)
        assert set(nightly_mod._resolve_symbols("SEED")) == set(SEED_SYMBOLS)

    def test_empty_defaults_to_active_universe(self) -> None:
        from unittest.mock import patch
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"PG"},
        ):
            assert nightly_mod._resolve_symbols("") == ["PG"]

    def test_csv_list(self) -> None:
        assert nightly_mod._resolve_symbols("AAPL,NVDA,msft") == ["AAPL", "NVDA", "MSFT"]

    def test_whitespace_tolerant(self) -> None:
        assert nightly_mod._resolve_symbols("AAPL , NVDA ,") == ["AAPL", "NVDA"]


class TestGating:
    def test_disabled_returns_gated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import settings

        monkeypatch.setattr(settings, "silver_ohlcv_build_enabled", False)
        gated, why = nightly_mod._silver_build_gated()
        assert gated is True
        assert "SILVER_OHLCV_BUILD_ENABLED" in why

    def test_missing_bucket_gated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import settings

        monkeypatch.setattr(settings, "silver_ohlcv_build_enabled", True)
        monkeypatch.setattr(settings, "stock_lake_bucket", "")
        gated, why = nightly_mod._silver_build_gated()
        assert gated is True
        assert "STOCK_LAKE_BUCKET" in why

    def test_enabled_with_bucket_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import settings

        monkeypatch.setattr(settings, "silver_ohlcv_build_enabled", True)
        monkeypatch.setattr(settings, "stock_lake_bucket", "some-bucket")
        monkeypatch.setattr(settings, "iceberg_glue_database", "stock_lake")
        gated, why = nightly_mod._silver_build_gated()
        assert gated is False
        assert why == ""


# ─────────────────────────────────────────────────────────────────────
# run_silver_ohlcv_build_nightly — summary shape + thread offload
# ─────────────────────────────────────────────────────────────────────


class _FakeBuild:
    """Stand-in for SilverOhlcvBuild so the test stays at the loop level
    (doesn't exercise PyIceberg, just the wrapper)."""

    def __init__(self, *, slices_succeeded: int = 5, slices_failed: int = 0) -> None:
        self.from_settings_called = False
        self._slices_succeeded = slices_succeeded
        self._slices_failed = slices_failed
        self.run_nightly_arg: list[str] | None = None

    def run_nightly(self, symbols: list[str]) -> BuildResult:
        self.run_nightly_arg = list(symbols)
        t = datetime(2024, 6, 11, tzinfo=timezone.utc)
        slices = [
            SliceResult(
                symbol=sym, date=date(2024, 6, 10),
                silver_rows_written=388,
            )
            for sym in symbols[: self._slices_succeeded]
        ] + [
            SliceResult(
                symbol=sym, date=date(2024, 6, 10),
                error="fail",
            )
            for sym in symbols[
                self._slices_succeeded: self._slices_succeeded + self._slices_failed
            ]
        ]
        return BuildResult(
            run_id="test-run-id",
            started_at=t,
            finished_at=t + timedelta(seconds=42),
            symbols=symbols,
            start_date=date(2024, 6, 10),
            end_date=date(2024, 6, 10),
            slices=slices,
        )


class TestRunNightly:
    @pytest.mark.asyncio
    async def test_gated_returns_skipped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings

        monkeypatch.setattr(settings, "silver_ohlcv_build_enabled", False)
        result = await nightly_mod.run_silver_ohlcv_build_nightly()
        assert result["skipped"] is True
        assert "SILVER_OHLCV_BUILD_ENABLED" in result["reason"]

    @pytest.mark.asyncio
    async def test_runs_and_returns_summary(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings

        monkeypatch.setattr(settings, "silver_ohlcv_build_enabled", True)
        monkeypatch.setattr(settings, "stock_lake_bucket", "test-bucket")
        monkeypatch.setattr(settings, "iceberg_glue_database", "stock_lake")
        monkeypatch.setattr(settings, "silver_ohlcv_build_symbols", "AAPL,NVDA,MSFT")

        fake_build = _FakeBuild(slices_succeeded=3, slices_failed=0)
        monkeypatch.setattr(
            nightly_mod.SilverOhlcvBuild,
            "from_settings",
            classmethod(lambda cls: fake_build),
        )

        summary = await nightly_mod.run_silver_ohlcv_build_nightly()

        assert summary["run_id"] == "test-run-id"
        assert summary["symbols"] == 3
        assert summary["slices"] == 3
        assert summary["slices_succeeded"] == 3
        assert summary["slices_failed"] == 0
        assert summary["silver_rows"] == 388 * 3
        assert summary["duration_seconds"] == 42.0
        # The wrapper called run_nightly with the resolved symbols.
        assert fake_build.run_nightly_arg == ["AAPL", "NVDA", "MSFT"]


class TestLoopGating:
    @pytest.mark.asyncio
    async def test_loop_returns_immediately_when_gated(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings

        monkeypatch.setattr(settings, "silver_ohlcv_build_enabled", False)
        # Should return without entering the while True (otherwise the
        # test would hang on asyncio.sleep).
        await nightly_mod.run_silver_ohlcv_build_loop()
