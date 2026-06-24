"""EW-3: elliott_wave_labels row mapping + arrow schema (no AWS)."""
from __future__ import annotations

import datetime as dt
import json

import pyarrow as pa

from app.indicators.pivots import PivotDetector
from app.services.elliott_store.schema import (
    ELLIOTT_WAVE_LABELS_ARROW,
    asset_class_for,
    labeling_to_row,
)
from app.signals.elliott import WaveEngine
from tests.support.ewt_synthetic import AS_OF_WAVE3, synthetic_ohlc


def _labeling():
    close, high, low = synthetic_ohlc("up")
    piv = PivotDetector(period=3, source="hl").detect(close, high, low)
    return WaveEngine().label(piv, last_price=float(close.iloc[AS_OF_WAVE3]),
                              symbol="AAPL", interval="1d", as_of_index=AS_OF_WAVE3,
                              as_of=close.index[AS_OF_WAVE3].to_pydatetime())


def test_asset_class_routing():
    assert asset_class_for("AAPL") == "equity"
    assert asset_class_for("/ES") == "future"


def test_row_has_all_columns_and_correct_types():
    row = labeling_to_row(_labeling(), git_sha="abc123")
    # every arrow column present
    assert set(row.keys()) == set(ELLIOTT_WAVE_LABELS_ARROW.names)
    assert row["symbol"] == "AAPL"
    assert isinstance(row["as_of_date"], dt.date)
    assert row["interval"] == "1d"
    assert row["engine_ver"].startswith("ew")
    assert row["git_sha"] == "abc123"


def test_primary_columns_populated():
    row = labeling_to_row(_labeling())
    assert row["p_structure"] == "impulse"
    assert row["p_current_wave"] == "3"
    assert row["p_invalidation"] < row["p_confidence"] * 1e9  # sanity, numeric
    json.loads(row["p_targets"])   # valid JSON
    json.loads(row["p_pivots"])    # valid JSON


def test_row_builds_into_arrow_table():
    # the real schema-compatibility check: from_pylist enforces every type
    row = labeling_to_row(_labeling(), git_sha="x")
    tbl = pa.Table.from_pylist([row], schema=ELLIOTT_WAVE_LABELS_ARROW)
    assert tbl.num_rows == 1
    assert tbl.schema.field("as_of_date").type == pa.date32()
