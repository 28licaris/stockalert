"""
Tests for the verify-mutation guards added to
`scripts/run_silver_ohlcv_build.py` (docs/standards/coding.md rule 1E).

What's pinned here
==================
- `_enforce_mutation_contract` raises when build claims rows but
  silver.ohlcv_1m snapshot didn't advance — the silent-failure
  signature we want to catch loudly.
- `_enforce_mutation_contract` is a no-op when:
  - Build claims 0 rows (legitimate empty case).
  - Snapshot ID did advance (normal success path).
- `_capture_silver_state` handles missing tables gracefully (returns
  snapshot_id=None, rows=0) — important for fresh-install runs.

If any future refactor removes the guard or replaces it with a silent
no-op, these tests fail loudly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "scripts"))

import run_silver_ohlcv_build as r  # noqa: E402


class TestEnforceMutationContract:
    """The guard MUST raise when build claims success but the snapshot
    didn't change. This is the exact pattern that bit us with
    bronze.polygon_corp_actions on 2026-05-18."""

    def test_raises_when_rows_claimed_but_snapshot_unchanged(self) -> None:
        """The silent-failure signature: silver_rows > 0 but pre_snap == post_snap."""
        pre = {
            "ohlcv_1m": {"snapshot_id": "abc123", "rows": 0},
            "bar_quality": {"snapshot_id": None, "rows": 0},
        }
        post = {
            "ohlcv_1m": {"snapshot_id": "abc123", "rows": 0},  # unchanged!
            "bar_quality": {"snapshot_id": None, "rows": 0},
        }
        result_summary = {"silver_rows": 1_000_000}  # claims to have written

        with pytest.raises(RuntimeError, match="NO-OP detected"):
            r._enforce_mutation_contract(result_summary, pre, post)

    def test_passes_when_snapshot_advanced(self) -> None:
        """Normal success path: snapshot ID changes when rows are written."""
        pre = {
            "ohlcv_1m": {"snapshot_id": "abc123", "rows": 0},
            "bar_quality": {"snapshot_id": None, "rows": 0},
        }
        post = {
            "ohlcv_1m": {"snapshot_id": "def456", "rows": 65_000_000},
            "bar_quality": {"snapshot_id": "xyz789", "rows": 50_000},
        }
        result_summary = {"silver_rows": 65_000_000}

        # Should NOT raise
        r._enforce_mutation_contract(result_summary, pre, post)

    def test_passes_when_zero_rows_claimed(self) -> None:
        """Legitimate no-op (e.g. nightly with no new bars to write).

        If the build genuinely had nothing to write, snapshot unchanged
        is fine — we don't want false positives on idempotent re-runs."""
        pre = {
            "ohlcv_1m": {"snapshot_id": "abc123", "rows": 65_000_000},
            "bar_quality": {"snapshot_id": "xyz789", "rows": 50_000},
        }
        post = pre.copy()  # nothing changed
        result_summary = {"silver_rows": 0}  # claimed nothing

        # Should NOT raise — 0 claimed + 0 advanced = consistent
        r._enforce_mutation_contract(result_summary, pre, post)

    def test_passes_when_silver_rows_key_missing(self) -> None:
        """Defensive: if `silver_rows` is missing from the summary,
        treat as 0 (don't crash on the guard itself)."""
        pre = {
            "ohlcv_1m": {"snapshot_id": "abc123", "rows": 0},
            "bar_quality": {"snapshot_id": None, "rows": 0},
        }
        post = pre.copy()
        result_summary = {}  # no silver_rows key

        r._enforce_mutation_contract(result_summary, pre, post)


class TestCaptureSilverState:
    """Test _capture_silver_state via mocked PyIceberg.

    Real Iceberg integration is exercised by the actual silver build;
    here we just confirm the helper handles common failure modes
    without crashing the run."""

    def test_missing_tables_return_empty_state(self) -> None:
        """When neither silver table exists yet (fresh install),
        capture should return snapshot_id=None, rows=0 for both."""
        from unittest.mock import MagicMock, patch
        from pyiceberg.exceptions import NoSuchTableError

        fake_cat = MagicMock()
        fake_cat.load_table.side_effect = NoSuchTableError("missing")

        with patch("pyiceberg.catalog.load_catalog", return_value=fake_cat):
            state = r._capture_silver_state()

        for short in ("ohlcv_1m", "bar_quality"):
            assert state[short]["snapshot_id"] is None
            assert state[short]["rows"] == 0

    def test_empty_table_returns_none_snapshot(self) -> None:
        """Table exists but no snapshot (just-created, never written)."""
        from unittest.mock import MagicMock, patch

        fake_table = MagicMock()
        fake_table.current_snapshot.return_value = None

        fake_cat = MagicMock()
        fake_cat.load_table.return_value = fake_table

        with patch("pyiceberg.catalog.load_catalog", return_value=fake_cat):
            state = r._capture_silver_state()

        for short in ("ohlcv_1m", "bar_quality"):
            assert state[short]["snapshot_id"] is None
            assert state[short]["rows"] == 0

    def test_populated_table_returns_snapshot_and_rows(self) -> None:
        """Happy path: table has data, capture returns the actual state."""
        from unittest.mock import MagicMock, patch

        fake_snap = MagicMock()
        fake_snap.snapshot_id = 1234567890
        fake_snap.summary.additional_properties = {"total-records": "65000000"}

        fake_table = MagicMock()
        fake_table.current_snapshot.return_value = fake_snap

        fake_cat = MagicMock()
        fake_cat.load_table.return_value = fake_table

        with patch("pyiceberg.catalog.load_catalog", return_value=fake_cat):
            state = r._capture_silver_state()

        for short in ("ohlcv_1m", "bar_quality"):
            assert state[short]["snapshot_id"] == "1234567890"
            assert state[short]["rows"] == 65_000_000

    def test_load_table_exception_is_soft(self) -> None:
        """Unexpected exception (not NoSuchTableError) is caught + logged
        but doesn't crash the run — verify-mutation is best-effort.

        Rule 1F still applies — we log the warning. Rule 1E is "raise
        loudly when mutation didn't happen" — but the meta-check itself
        is allowed to soft-fail so the main run doesn't crash on
        observability infra."""
        from unittest.mock import MagicMock, patch

        fake_cat = MagicMock()
        fake_cat.load_table.side_effect = ConnectionError("Glue unreachable")

        with patch("pyiceberg.catalog.load_catalog", return_value=fake_cat):
            state = r._capture_silver_state()

        # Should not have raised; state should reflect the soft failure
        for short in ("ohlcv_1m", "bar_quality"):
            assert state[short]["snapshot_id"] is None
            assert state[short]["rows"] == 0
            assert "error" in state[short]
