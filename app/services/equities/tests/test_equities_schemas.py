"""Unit tests for architecture-v2 equities Iceberg schemas (CV1).

These tests verify the schema/partition/sort definitions against the
spec in docs/architecture_v2/02_schema.md without touching AWS. They
exist to catch accidental drift: a one-character partition-count typo
would otherwise only surface when CV2 tries to write data.
"""
from __future__ import annotations

import pytest

pyiceberg = pytest.importorskip("pyiceberg")

from pyiceberg.transforms import BucketTransform, MonthTransform  # noqa: E402
from pyiceberg.types import (  # noqa: E402
    DateType,
    DoubleType,
    LongType,
    StringType,
    TimestamptzType,
)

from app.services.equities.schemas import (  # noqa: E402
    MARKET_CORP_ACTIONS_PARTITION,
    MARKET_CORP_ACTIONS_SCHEMA,
    POLYGON_BUCKET_COUNT,
    POLYGON_RAW_PARTITION,
    POLYGON_RAW_SCHEMA,
    SCHWAB_UNIVERSE_PARTITION,
    SCHWAB_UNIVERSE_SCHEMA,
    equities_table_id,
)


# Canonical OHLCV column names + types — every bar table must have these.
_OHLCV_BASE = [
    ("symbol", StringType(), True),
    ("timestamp", TimestamptzType(), True),
    ("open", DoubleType(), False),
    ("high", DoubleType(), False),
    ("low", DoubleType(), False),
    ("close", DoubleType(), False),
    ("volume", DoubleType(), False),
    ("vwap", DoubleType(), False),
    ("trade_count", LongType(), False),
    ("source", StringType(), False),
    ("ingestion_ts", TimestamptzType(), False),
    ("ingestion_run_id", StringType(), False),
]


def _column_summary(schema):
    return [(f.name, f.field_type, f.required) for f in schema.fields]


def _identifier_names(schema):
    return [schema.find_field(fid).name for fid in schema.identifier_field_ids]


# ─────────────────────────────────────────────────────────────────────
# polygon_raw
# ─────────────────────────────────────────────────────────────────────

def test_polygon_raw_columns_match_spec():
    assert _column_summary(POLYGON_RAW_SCHEMA) == _OHLCV_BASE


def test_polygon_raw_identifier_is_symbol_timestamp():
    assert _identifier_names(POLYGON_RAW_SCHEMA) == ["symbol", "timestamp"]


def test_polygon_raw_partition_is_bucket32_monthts():
    fields = POLYGON_RAW_PARTITION.fields
    assert len(fields) == 2
    assert fields[0].name == "symbol_bucket"
    assert isinstance(fields[0].transform, BucketTransform)
    assert fields[0].transform.num_buckets == POLYGON_BUCKET_COUNT == 32
    assert fields[1].name == "ts_month"
    assert isinstance(fields[1].transform, MonthTransform)


def test_polygon_raw_has_no_adj_factor():
    names = {f.name for f in POLYGON_RAW_SCHEMA.fields}
    assert "adj_factor" not in names, "raw bars are unadjusted by definition"


# polygon_adjusted: RETIRED — adjusted OHLCV is computed at read time
# (app.services.equities.adjust), not stored. Its schema tests were removed
# with the table (docs/adjusted_lean_storage_spec.md). The read-time output
# shape is covered by app/services/equities/tests/test_adjust.py.


# ─────────────────────────────────────────────────────────────────────
# schwab_universe
# ─────────────────────────────────────────────────────────────────────

def test_schwab_universe_carries_adj_factor():
    field = SCHWAB_UNIVERSE_SCHEMA.find_field("adj_factor")
    assert isinstance(field.field_type, DoubleType)
    assert field.required is True


def test_schwab_universe_partitions_by_month_only():
    """schwab_universe is the recent rolling window of the active
    universe (~hundreds of symbols), so it partitions by month(timestamp)
    ONLY — no symbol bucketing. Bucketing would just fan each nightly
    write across N files; month-only yields ~1 file/month + the symbol
    sort order handles per-symbol pruning. (polygon_adjusted, the whole
    33K-symbol market, keeps bucket(32).)"""
    fields = SCHWAB_UNIVERSE_PARTITION.fields
    assert len(fields) == 1, "no symbol bucketing — small rolling table"
    assert fields[0].name == "ts_month"
    assert str(fields[0].transform) == "month"


# ─────────────────────────────────────────────────────────────────────
# market_corp_actions
# ─────────────────────────────────────────────────────────────────────

def test_market_corp_actions_identifier_is_symbol_exdate_action():
    assert _identifier_names(MARKET_CORP_ACTIONS_SCHEMA) == [
        "symbol",
        "ex_date",
        "action_type",
    ]


def test_market_corp_actions_columns_match_spec():
    expected = [
        ("symbol", StringType(), True),
        ("ex_date", DateType(), True),
        ("action_type", StringType(), True),
        ("factor", DoubleType(), False),
        ("cash_amount", DoubleType(), False),
        ("announced_at", TimestamptzType(), False),
        ("source_provider", StringType(), True),
        ("raw_payload", StringType(), False),
        ("ingestion_ts", TimestamptzType(), False),
        ("ingestion_run_id", StringType(), False),
    ]
    assert _column_summary(MARKET_CORP_ACTIONS_SCHEMA) == expected


def test_market_corp_actions_partitioned_by_ex_month_only():
    fields = MARKET_CORP_ACTIONS_PARTITION.fields
    assert len(fields) == 1, "no symbol bucketing — tiny table"
    assert fields[0].name == "ex_month"
    assert isinstance(fields[0].transform, MonthTransform)


# ─────────────────────────────────────────────────────────────────────
# Identifier helper
# ─────────────────────────────────────────────────────────────────────

def test_equities_table_id_uses_settings_db(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "iceberg_equities_glue_database", "equities_test")
    assert equities_table_id("polygon_raw") == "equities_test.polygon_raw"
