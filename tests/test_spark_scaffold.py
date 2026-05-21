"""Unit tests for `scripts/spark/__init__.py` (CV5).

Covers the pure-Python helpers (`record_run`, `get_spark` env-var
contract). The actual SparkSession construction needs pyspark
installed and is gated behind `pytest.importorskip`.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

# Add repo root to sys.path so the `scripts` package is importable.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.spark import get_spark, record_run  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# get_spark() env-var contract
# ─────────────────────────────────────────────────────────────────────

def test_get_spark_raises_when_warehouse_env_missing(monkeypatch):
    """No warehouse path = no safe default; must hard-fail before
    Spark spins up (we don't want jobs writing to the wrong place
    because someone forgot an env var)."""
    monkeypatch.delenv("STOCK_LAKE_BUCKET_S3", raising=False)
    with pytest.raises(RuntimeError, match="STOCK_LAKE_BUCKET_S3"):
        get_spark("test-app")


def test_get_spark_raises_when_warehouse_env_empty_string(monkeypatch):
    monkeypatch.setenv("STOCK_LAKE_BUCKET_S3", "   ")
    with pytest.raises(RuntimeError, match="STOCK_LAKE_BUCKET_S3"):
        get_spark("test-app")


# Full SparkSession construction needs pyspark — only available in the
# `spark` poetry group. Skip otherwise.
def test_get_spark_builds_session_when_pyspark_available(monkeypatch):
    pyspark = pytest.importorskip("pyspark")
    monkeypatch.setenv("STOCK_LAKE_BUCKET_S3", "s3://stockalert-lake-test/iceberg/")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("STOCKALERT_SPARK_LOCAL_MODE", raising=False)

    spark = get_spark("stockalert-test")
    try:
        assert spark.sparkContext.appName == "stockalert-test"
        # Config plumb-through — catalog name + warehouse env reach Spark.
        assert spark.conf.get("spark.sql.catalog.lake.warehouse") == (
            "s3://stockalert-lake-test/iceberg/"
        )
        assert "Iceberg" in spark.conf.get("spark.sql.extensions")
    finally:
        spark.stop()


# ─────────────────────────────────────────────────────────────────────
# record_run() — structured log emission
# ─────────────────────────────────────────────────────────────────────

def test_record_run_ok_logs_info_with_stable_keys(caplog):
    caplog.set_level("INFO")
    started = time.time() - 5  # pretend the job ran for 5s

    rec = record_run(
        job_name="polygon_adjustment_job",
        status="ok",
        rows_written=42,
        symbols_processed=3,
        started_at=started,
    )

    assert rec["job"] == "polygon_adjustment_job"
    assert rec["status"] == "ok"
    assert rec["rows_written"] == 42
    assert rec["symbols_processed"] == 3
    assert rec["duration_s"] is not None and rec["duration_s"] >= 4.0
    assert rec["error"] is None
    # The log line must be JSON so CloudWatch / DataDog can parse it.
    log_lines = [r.message for r in caplog.records if "spark_run" in r.message]
    assert log_lines, "expected a spark_run log line"
    payload = log_lines[0].removeprefix("spark_run ").strip()
    parsed = json.loads(payload)
    assert parsed["job"] == "polygon_adjustment_job"


def test_record_run_error_logs_error_level(caplog):
    caplog.set_level("ERROR")
    rec = record_run(
        job_name="polygon_adjustment_job",
        status="error",
        error="join blew up",
    )
    assert rec["status"] == "error"
    assert rec["error"] == "join blew up"
    err_lines = [r for r in caplog.records if r.levelname == "ERROR"]
    assert err_lines, "expected error-level log when status=error"


def test_record_run_without_started_at_omits_duration():
    rec = record_run(job_name="t", status="ok")
    assert rec["duration_s"] is None


def test_record_run_extra_fields_merged_into_record():
    rec = record_run(
        job_name="t", status="ok",
        extra={"spark_app_id": "app-123", "executor_count": 4},
    )
    assert rec["spark_app_id"] == "app-123"
    assert rec["executor_count"] == 4
