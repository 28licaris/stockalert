"""Unit tests for `scripts/spark/polygon_adjustment_job.py` CLI plumbing.

Only covers the pure-Python helpers (`_parse_symbols`, `_parse_date`,
`main()` error paths). The Spark execution (`adjust()`) needs pyspark
+ Glue + S3 and is exercised by an integration test guarded by
`pytest.mark.integration` (not in this file).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.spark import polygon_adjustment_job as job  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# _parse_symbols
# ─────────────────────────────────────────────────────────────────────

def test_parse_symbols_none_means_whole_market():
    assert job._parse_symbols(None) is None
    assert job._parse_symbols("") is None
    assert job._parse_symbols("   ") is None


def test_parse_symbols_all_means_whole_market():
    assert job._parse_symbols("ALL") is None
    assert job._parse_symbols("all") is None


def test_parse_symbols_uppercases_and_strips():
    assert job._parse_symbols("aapl,msft") == ["AAPL", "MSFT"]
    assert job._parse_symbols(" aapl ,  nvda ") == ["AAPL", "NVDA"]


def test_parse_symbols_drops_empty_entries():
    assert job._parse_symbols(",AAPL,,NVDA,") == ["AAPL", "NVDA"]


def test_parse_symbols_empty_after_strip_returns_none():
    assert job._parse_symbols(",,") is None


# ─────────────────────────────────────────────────────────────────────
# _parse_date
# ─────────────────────────────────────────────────────────────────────

def test_parse_date_none_passes_through():
    assert job._parse_date(None) is None
    assert job._parse_date("") is None


def test_parse_date_iso_parsed():
    assert job._parse_date("2024-05-13") == date(2024, 5, 13)


def test_parse_date_invalid_raises():
    with pytest.raises(ValueError):
        job._parse_date("not-a-date")


# ─────────────────────────────────────────────────────────────────────
# main() — error paths (Spark calls fully mocked)
# ─────────────────────────────────────────────────────────────────────

def test_main_returns_1_when_ensure_target_fails():
    with patch.object(job, "_ensure_target_exists", side_effect=RuntimeError("glue 500")), \
         patch.object(job, "adjust") as mocked_adjust, \
         patch.object(job, "record_run") as mocked_record:
        rc = job.main(["--symbols", "AAPL"])

    assert rc == 1
    mocked_adjust.assert_not_called()
    mocked_record.assert_called_once()
    assert mocked_record.call_args.kwargs["status"] == "error"
    assert "ensure_target_failed" in mocked_record.call_args.kwargs["error"]


def test_main_returns_1_when_adjust_raises():
    with patch.object(job, "_ensure_target_exists"), \
         patch.object(job, "adjust", side_effect=RuntimeError("join blew up")), \
         patch.object(job, "record_run") as mocked_record:
        rc = job.main(["--symbols", "AAPL", "--since", "2024-01-01"])

    assert rc == 1
    mocked_record.assert_called_once()
    assert mocked_record.call_args.kwargs["status"] == "error"
    assert "join blew up" in mocked_record.call_args.kwargs["error"]


def test_main_returns_0_on_success_and_records_metrics():
    with patch.object(job, "_ensure_target_exists"), \
         patch.object(job, "adjust", return_value=(3, 8_400_000)), \
         patch.object(job, "record_run") as mocked_record:
        rc = job.main(["--symbols", "AAPL,MSFT,NVDA", "--since", "2024-01-01"])

    assert rc == 0
    mocked_record.assert_called_once()
    kwargs = mocked_record.call_args.kwargs
    assert kwargs["status"] == "ok"
    assert kwargs["rows_written"] == 8_400_000
    assert kwargs["symbols_processed"] == 3
    assert kwargs["job_name"] == "polygon_adjustment_job"


def test_main_passes_symbols_and_dates_to_adjust():
    with patch.object(job, "_ensure_target_exists"), \
         patch.object(job, "adjust", return_value=(1, 100)) as mocked_adjust, \
         patch.object(job, "record_run"):
        job.main([
            "--symbols", "AAPL",
            "--since", "2024-01-02",
            "--until", "2024-12-31",
        ])

    args, _ = mocked_adjust.call_args
    assert args == (["AAPL"], date(2024, 1, 2), date(2024, 12, 31))


def test_main_whole_market_passes_none_to_adjust():
    """--symbols ALL or omitted must propagate None — `adjust()`
    interprets None as whole-market (no symbol filter applied to the
    Spark DataFrame)."""
    with patch.object(job, "_ensure_target_exists"), \
         patch.object(job, "adjust", return_value=(0, 0)) as mocked_adjust, \
         patch.object(job, "record_run"):
        job.main(["--symbols", "ALL"])

    args, _ = mocked_adjust.call_args
    assert args[0] is None  # symbols=None
