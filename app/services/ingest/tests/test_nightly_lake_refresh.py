"""Unit tests for nightly Polygon → S3 lake refresh helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.ingest.nightly_polygon_refresh import (
    _parse_nightly_kind,
    _seconds_until_next_run,
    resolve_nightly_lake_symbols,
)


def test_seconds_until_next_same_calendar_day():
    now = datetime(2026, 5, 14, 6, 0, 0, tzinfo=timezone.utc)
    s = _seconds_until_next_run(7, now=now)
    assert s == pytest.approx(3600.0, abs=0.01)


def test_seconds_until_next_rolls_to_next_day():
    now = datetime(2026, 5, 14, 8, 0, 0, tzinfo=timezone.utc)
    s = _seconds_until_next_run(7, now=now)
    nxt = datetime(2026, 5, 15, 7, 0, 0, tzinfo=timezone.utc)
    assert s == pytest.approx((nxt - now).total_seconds(), abs=0.5)


def test_seconds_until_next_exact_boundary():
    now = datetime(2026, 5, 14, 7, 0, 0, tzinfo=timezone.utc)
    s = _seconds_until_next_run(7, now=now)
    assert s >= 86400.0 - 1.0


def test_resolve_seed_is_rejected():
    with pytest.raises(ValueError, match="static seed universe is retired"):
        resolve_nightly_lake_symbols("seed")


def test_resolve_explicit_and_all():
    assert resolve_nightly_lake_symbols("aapl, msft") == ["AAPL", "MSFT"]
    assert resolve_nightly_lake_symbols("all") == []


def test_parse_nightly_kind_variants():
    assert _parse_nightly_kind("minute") == ("minute",)
    assert _parse_nightly_kind("DAY") == ("day",)
    assert _parse_nightly_kind("both") == ("minute", "day")


# ─────────────────────────────────────────────────────────────────────
# CV7 — v2 cutover regression tests
#
# These don't exercise the full Polygon → Iceberg flow (that needs
# AWS creds + Polygon creds — covered by integration tests). They
# verify the imports + wiring are pointed at the v2 surface so a
# careless future edit can't accidentally revert to bronze.
# ─────────────────────────────────────────────────────────────────────

from unittest.mock import patch, AsyncMock, MagicMock  # noqa: E402

from app.services.ingest import nightly_polygon_refresh as nightly  # noqa: E402


def test_module_binds_v2_sink_not_bronze():
    """Symbolic check: the module exports EquitiesIcebergSink and has
    no bronze imports bound. Catches accidental rollback to v1."""
    assert hasattr(nightly, "EquitiesIcebergSink"), "v2 sink import missing"
    assert not hasattr(nightly, "BronzeIcebergSink"), \
        "v1 sink still bound at module level"
    assert not hasattr(nightly, "WatermarkRepo"), \
        "v1 CH-watermark dependency must be dropped (gap pre-scan is the v2 source of truth)"


@pytest.mark.asyncio
async def test_refresh_target_path_uses_equities_sink(monkeypatch):
    """When refresh_polygon_lake_yesterday is called with target=date,
    the sink it constructs MUST be EquitiesIcebergSink.for_polygon_raw."""
    from datetime import date

    # Enable the gate.
    monkeypatch.setattr(nightly.settings, "polygon_nightly_enabled", True)
    monkeypatch.setattr(nightly.settings, "stock_lake_bucket", "test-bucket")
    monkeypatch.setattr(nightly.settings, "polygon_nightly_symbols", "AAPL")
    monkeypatch.setattr(nightly.settings, "polygon_nightly_kind", "minute")

    sink_factory = MagicMock(return_value=MagicMock(name="equities_polygon_raw"))
    monkeypatch.setattr(
        nightly.EquitiesIcebergSink, "for_polygon_raw", sink_factory,
    )
    monkeypatch.setattr(
        nightly.PolygonFlatFilesClient, "from_settings",
        classmethod(lambda cls: MagicMock(name="ff_client")),
    )

    # Mock the backfill service so we capture how it's constructed.
    # Must be a class-like mock — the code reads
    # FlatFilesBackfillService.DEFAULT_SOURCE_TAG as a class attribute.
    fake_svc = MagicMock()
    fake_result = MagicMock()
    fake_result.to_summary.return_value = {"days_ok": 1, "bars_persisted": 100}
    fake_svc.backfill_range = AsyncMock(return_value=fake_result)

    captured_kwargs: dict = {}

    class FakeServiceClass:
        DEFAULT_SOURCE_TAG = "polygon-flatfiles"

        def __new__(cls, *args, **kwargs):
            captured_kwargs.update(kwargs)
            return fake_svc

    monkeypatch.setattr(nightly, "FlatFilesBackfillService", FakeServiceClass)
    # Also patch the loaded_dates_in_range import inside the per-day
    # loop so the test doesn't try to scan a real Iceberg table.
    monkeypatch.setattr(
        "app.services.equities.gaps.loaded_dates_in_range",
        lambda *a, **k: set(),
    )
    monkeypatch.setattr(
        "app.services.equities.tables.ensure_polygon_raw",
        lambda *a, **k: MagicMock(),
    )

    result = await nightly.refresh_polygon_lake_yesterday(target=date(2024, 5, 14))

    sink_factory.assert_called_once_with()
    assert "sinks" in captured_kwargs
    assert len(captured_kwargs["sinks"]) == 1
    # The single sink IS the one the v2 factory returned.
    assert captured_kwargs["sinks"][0] is sink_factory.return_value
    assert "date" in result
    assert result["date"] == "2024-05-14"


@pytest.mark.asyncio
async def test_refresh_gated_off_when_lake_disabled(monkeypatch):
    """When POLYGON_NIGHTLY_ENABLED is false, must short-circuit
    without touching any v2 helper — keeps the v1-disabled deploy
    behaviour identical."""
    monkeypatch.setattr(nightly.settings, "polygon_nightly_enabled", False)

    result = await nightly.refresh_polygon_lake_yesterday()

    assert result["skipped"] is True
    assert "lake disabled" in result["reason"]
