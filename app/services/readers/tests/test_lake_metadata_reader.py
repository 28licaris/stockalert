"""Unit tests for LakeMetadataReader (CV29).

Mocks PyIceberg's Table + Snapshot interfaces so the suite runs
offline. Real snapshots are exercised by the integration test
(gated by AWS creds).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

pyiceberg = pytest.importorskip("pyiceberg")

from app.services.readers.lake_metadata_reader import (  # noqa: E402
    LakeMetadataReader,
    _committed_at_dt,
    _snapshot_to_model,
)


def _fake_snapshot(
    *,
    snapshot_id: int,
    timestamp_ms: int,
    operation: str | None = "append",
    total_records: int | None = 1000,
    added_records: int | None = 100,
    parent_snapshot_id: int | None = None,
):
    """Build a PyIceberg snapshot stand-in. Summary access mimics
    the real type — `dict(summary)` gives the counter map; .operation
    is the snapshot operation tag."""
    summary = MagicMock()
    summary.operation = operation
    # The real Summary supports dict()-coercion via .additional_properties
    # or by being a Mapping. Mock it via __iter__ + __getitem__ so
    # `dict(summary)` works.
    counters: dict = {}
    if total_records is not None:
        counters["total-records"] = str(total_records)
    if added_records is not None:
        counters["added-records"] = str(added_records)
    summary.keys.return_value = list(counters.keys())
    summary.__iter__.side_effect = lambda: iter(counters)
    summary.__getitem__.side_effect = counters.__getitem__
    summary.get = counters.get

    snap = MagicMock()
    snap.snapshot_id = snapshot_id
    snap.timestamp_ms = timestamp_ms
    snap.parent_snapshot_id = parent_snapshot_id
    snap.summary = summary
    return snap


def _fake_table(snapshots: list, *, snapshots_raises: Exception | None = None):
    t = MagicMock()
    if snapshots_raises is not None:
        t.snapshots.side_effect = snapshots_raises
    else:
        t.snapshots.return_value = snapshots
    return t


def _fake_catalog(table_map: dict[str, object], *, load_raises_for: set[str] | None = None):
    cat = MagicMock()
    load_raises_for = load_raises_for or set()

    def _load(fq: str):
        if fq in load_raises_for:
            raise RuntimeError(f"glue 500 for {fq}")
        if fq in table_map:
            return table_map[fq]
        raise KeyError(f"no fake for {fq}")

    cat.load_table.side_effect = _load
    return cat


# ─────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────


def test_committed_at_dt_converts_ms_to_utc_datetime():
    snap = MagicMock()
    snap.timestamp_ms = 1_700_000_000_000  # 2023-11-14 22:13:20 UTC
    dt = _committed_at_dt(snap)
    assert dt == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_snapshot_to_model_extracts_counters():
    snap = _fake_snapshot(
        snapshot_id=42, timestamp_ms=1_700_000_000_000,
        total_records=8_400_000, added_records=50_000,
        parent_snapshot_id=41,
    )
    model = _snapshot_to_model("equities.polygon_raw", snap)
    assert model.snapshot_id == 42
    assert model.table_name == "equities.polygon_raw"
    assert model.total_records == 8_400_000
    assert model.added_records == 50_000
    assert model.parent_snapshot_id == 41
    assert model.operation == "append"
    assert model.committed_at.tzinfo is not None


def test_snapshot_to_model_handles_missing_counters():
    snap = _fake_snapshot(
        snapshot_id=1, timestamp_ms=1_700_000_000_000,
        total_records=None, added_records=None, parent_snapshot_id=None,
    )
    model = _snapshot_to_model("equities.market_corp_actions", snap)
    assert model.total_records is None
    assert model.added_records is None
    assert model.parent_snapshot_id is None


# ─────────────────────────────────────────────────────────────────────
# list_snapshots
# ─────────────────────────────────────────────────────────────────────


class TestListSnapshots:
    def test_default_lists_all_four_tables(self):
        snap_a = _fake_snapshot(snapshot_id=1, timestamp_ms=1_700_000_000_000)
        snap_b = _fake_snapshot(snapshot_id=2, timestamp_ms=1_700_000_100_000)
        table = _fake_table([snap_a, snap_b])
        catalog = _fake_catalog({
            "equities.polygon_raw": table,
            "equities.market_splits": table,
            "equities.schwab_universe": table,
            "equities.market_corp_actions": table,
        })
        reader = LakeMetadataReader(catalog=catalog)

        resp = reader.list_snapshots()

        # Every default table requested (polygon_adjusted retired in v2).
        assert resp.requested_tables == [
            "equities.polygon_raw",
            "equities.market_splits",
            "equities.schwab_universe",
            "equities.market_corp_actions",
        ]
        # 4 tables × 2 snaps each = 8 rows.
        assert resp.count == 8

    def test_unknown_short_name_skipped_with_warning(self):
        table = _fake_table([
            _fake_snapshot(snapshot_id=1, timestamp_ms=1_700_000_000_000),
        ])
        catalog = _fake_catalog({"equities.polygon_raw": table})
        reader = LakeMetadataReader(catalog=catalog)

        resp = reader.list_snapshots(tables=["polygon_raw", "typo_table"])
        assert resp.requested_tables == ["equities.polygon_raw"]
        assert resp.count == 1

    def test_load_failure_skips_table_only(self):
        good_table = _fake_table([
            _fake_snapshot(snapshot_id=1, timestamp_ms=1_700_000_000_000),
        ])
        catalog = _fake_catalog(
            {"equities.polygon_raw": good_table},
            load_raises_for={"equities.schwab_universe"},
        )
        reader = LakeMetadataReader(catalog=catalog)

        resp = reader.list_snapshots(
            tables=["polygon_raw", "schwab_universe"],
        )
        # Both requested, but the failed one only contributes 0 rows.
        assert resp.requested_tables == [
            "equities.polygon_raw",
            "equities.schwab_universe",
        ]
        assert resp.count == 1
        assert resp.snapshots[0].table_name == "equities.polygon_raw"

    def test_snapshots_iteration_failure_degrades_to_empty(self):
        bad_table = _fake_table(
            [], snapshots_raises=RuntimeError("metadata corrupt"),
        )
        catalog = _fake_catalog({"equities.polygon_raw": bad_table})
        reader = LakeMetadataReader(catalog=catalog)

        resp = reader.list_snapshots(tables=["polygon_raw"])
        assert resp.count == 0
        assert resp.requested_tables == ["equities.polygon_raw"]

    def test_per_table_limit_then_cross_sort(self):
        """Per-table limit applies BEFORE the cross-table sort so a
        tiny limit doesn't lose newer snapshots from one table to
        older noise on another."""
        # polygon_raw: 3 snaps spanning days
        raw_snaps = [
            _fake_snapshot(snapshot_id=10, timestamp_ms=1_700_000_000_000),
            _fake_snapshot(snapshot_id=11, timestamp_ms=1_700_086_400_000),  # +1 day
            _fake_snapshot(snapshot_id=12, timestamp_ms=1_700_172_800_000),  # +2 days
        ]
        # market_splits: 1 snap from yesterday
        adj_snaps = [
            _fake_snapshot(snapshot_id=20, timestamp_ms=1_700_129_600_000),
        ]
        catalog = _fake_catalog({
            "equities.polygon_raw": _fake_table(raw_snaps),
            "equities.market_splits": _fake_table(adj_snaps),
        })
        reader = LakeMetadataReader(catalog=catalog)

        resp = reader.list_snapshots(
            tables=["polygon_raw", "market_splits"], limit=2,
        )

        # Per-table limit=2: polygon_raw contributes its 2 NEWEST
        # (snapshot_id 12, 11). market_splits contributes its 1.
        # Total 3 snapshots, sorted DESC across tables.
        assert resp.count == 3
        ids = [s.snapshot_id for s in resp.snapshots]
        # 12 (raw, newest), 20 (splits), 11 (raw)
        assert ids == [12, 20, 11]

    def test_cross_table_sort_is_committed_at_desc(self):
        old = _fake_snapshot(snapshot_id=1, timestamp_ms=1_700_000_000_000)
        new = _fake_snapshot(snapshot_id=2, timestamp_ms=1_700_500_000_000)
        catalog = _fake_catalog({
            "equities.polygon_raw": _fake_table([old]),
            "equities.market_splits": _fake_table([new]),
        })
        reader = LakeMetadataReader(catalog=catalog)

        resp = reader.list_snapshots(
            tables=["polygon_raw", "market_splits"], limit=10,
        )
        assert resp.snapshots[0].snapshot_id == 2  # newer first
        assert resp.snapshots[1].snapshot_id == 1
