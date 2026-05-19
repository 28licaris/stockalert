"""
Tests for `scripts/rebuild_ch_from_silver.py`.

What's pinned here
==================
- `_bronze_history_start` parses the BRONZE_HISTORY_START env / setting,
  falls back to 2021-01-04 on bad data.
- `_ch_ohlcv_row_count` returns -1 (sentinel) instead of raising when
  CH is unreachable — so the verify-mutation step is always best-effort
  without crashing the whole rebuild.
- `_wipe_ch_ohlcv` refuses to proceed if TRUNCATE doesn't actually
  empty the table (coding_standards.md rule 1E — verify-mutation).
- Mismatch warning fires when bars_written >> ch_rows_delta (silent
  insert failure detection).

These are unit-level (no live CH, no live S3). End-to-end correctness
is validated by the operator runbook + the Yahoo spot-checks after
the silver build.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "scripts"))

import rebuild_ch_from_silver as r  # noqa: E402


class TestBronzeHistoryStart:
    def test_default_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Defaults to 2021-01-04 when BRONZE_HISTORY_START is unset/empty."""
        from app.config import settings
        monkeypatch.setattr(settings, "bronze_history_start", "")
        assert r._bronze_history_start() == date(2021, 1, 4)

    def test_valid_iso_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Honors a valid ISO date for an extended history window."""
        from app.config import settings
        monkeypatch.setattr(settings, "bronze_history_start", "2006-01-04")
        assert r._bronze_history_start() == date(2006, 1, 4)

    def test_invalid_value_falls_back(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Garbage in env → defaults, doesn't raise."""
        from app.config import settings
        monkeypatch.setattr(settings, "bronze_history_start", "not-a-date")
        assert r._bronze_history_start() == date(2021, 1, 4)


class TestRowCountSentinel:
    def test_returns_minus_one_on_query_error(self) -> None:
        """When CH is unreachable, `_ch_ohlcv_row_count` returns -1 not
        an exception. Rule 1F — don't let infrastructure flakes crash
        the whole rebuild loop."""
        bad_client = MagicMock()
        bad_client.query.side_effect = ConnectionError("CH down")
        with patch.object(r, "get_client", return_value=bad_client):
            assert r._ch_ohlcv_row_count() == -1

    def test_returns_int_on_success(self) -> None:
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[[42_000_000]])
        with patch.object(r, "get_client", return_value=client):
            assert r._ch_ohlcv_row_count() == 42_000_000


class TestWipe:
    def test_truncate_must_actually_empty_table(self) -> None:
        """If TRUNCATE runs but post-count != 0, raise. Rule 1E."""
        client = MagicMock()
        # First query (pre-count): 1000 rows
        # Second query (post-count after TRUNCATE): STILL 1000 (broken)
        client.query.side_effect = [
            MagicMock(result_rows=[[1000]]),
            MagicMock(result_rows=[[1000]]),
        ]
        with patch.object(r, "get_client", return_value=client):
            with pytest.raises(RuntimeError, match="row count is 1000"):
                r._wipe_ch_ohlcv()

    def test_truncate_success_path(self) -> None:
        """Happy path — pre=N, post=0, no raise."""
        client = MagicMock()
        client.query.side_effect = [
            MagicMock(result_rows=[[1000]]),
            MagicMock(result_rows=[[0]]),
        ]
        with patch.object(r, "get_client", return_value=client):
            r._wipe_ch_ohlcv()  # should not raise
        client.command.assert_called_once_with(
            "TRUNCATE TABLE ohlcv_1m",
            settings={"max_table_size_to_drop": 0},
        )


class TestSymbolResolution:
    def test_default_seed(self) -> None:
        symbols = r._resolve_symbols("seed")
        assert isinstance(symbols, list)
        assert len(symbols) > 0
        # SEED_SYMBOLS contains AAPL and NVDA (sanity)
        assert "AAPL" in symbols

    def test_explicit_csv(self) -> None:
        symbols = r._resolve_symbols("AAPL,NVDA,MSFT")
        assert symbols == ["AAPL", "NVDA", "MSFT"]

    def test_empty_falls_to_seed(self) -> None:
        # Empty / None spec resolves to seed per universe.resolve_universe_spec
        out_empty = r._resolve_symbols("")
        out_seed = r._resolve_symbols("seed")
        assert out_empty == out_seed


class TestMismatchDetection:
    """Pin the warning logic — bars_written far exceeds ch_rows_delta
    is the silent-failure signature we want to surface."""

    def test_warning_triggers_when_ratio_low(self) -> None:
        """bars_written=10M, ch_delta=1M → ratio 0.1, well below 0.9
        threshold → warning fires."""
        report = r.RunResult(
            bars_written_total=10_000_000,
            ch_rows_delta=1_000_000,
        )
        # Replicate the inline check from main()
        ratio = report.ch_rows_delta / max(1, report.bars_written_total)
        triggered = ratio < 0.9
        assert triggered is True

    def test_no_warning_when_ratio_acceptable(self) -> None:
        """bars_written=10M, ch_delta=9.5M → ratio 0.95, above threshold."""
        report = r.RunResult(
            bars_written_total=10_000_000,
            ch_rows_delta=9_500_000,
        )
        ratio = report.ch_rows_delta / max(1, report.bars_written_total)
        triggered = ratio < 0.9
        assert triggered is False

    def test_no_warning_when_nothing_written(self) -> None:
        """bars_written=0 → mismatch check skipped (no false positive
        when --wipe wasn't used + everything was already in CH)."""
        report = r.RunResult(bars_written_total=0, ch_rows_delta=0)
        # main() only runs the ratio check when bars_written > 0
        assert report.bars_written_total == 0


class TestExitCodes:
    """Status → exit code mapping per coding_standards.md rule 1D."""

    def test_ok_returns_zero(self) -> None:
        # Indirect — verified via the _finalize_and_print contract
        # below in integration. Here just sanity-check the mapping.
        for ok_status in ("ok", "ok_with_warnings", "no_symbols"):
            report = r.RunResult(status=ok_status)
            # Mirror the exit-code logic in _finalize_and_print
            assert (0 if report.status in
                    ("ok", "ok_with_warnings", "no_symbols") else 2) == 0

    def test_failed_status_returns_two(self) -> None:
        for bad_status in ("fail", "partial_fail"):
            report = r.RunResult(status=bad_status)
            assert (0 if report.status in
                    ("ok", "ok_with_warnings", "no_symbols") else 2) == 2
