"""
Offline tests for the production cold read path (read_arrow + registry).

Builds a REAL local Iceberg table (pyiceberg SqlCatalog + sqlite + file
warehouse) for two sources, so `pl.scan_iceberg` exercises the genuine
scan/pushdown/dedup path — no cloud, no Glue. Asserts the union/dedup
contract (polygon wins contested (symbol,timestamp); schwab fills gaps),
single-source selection, projection pushdown, and cold-start safety.

Skips cleanly if polars / pyiceberg-sql deps aren't present.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import pytest

pl = pytest.importorskip("polars")
pytest.importorskip("sqlalchemy")  # SqlCatalog backend

from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.schema import Schema
from pyiceberg.types import (
    DoubleType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from app.services.readers import read_arrow as ra
from app.services.readers.source_registry import SourceSpec, resolve_sources

UTC = timezone.utc

_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),
    NestedField(2, "timestamp", TimestamptzType(), required=True),
    NestedField(3, "open", DoubleType(), required=False),
    NestedField(4, "high", DoubleType(), required=False),
    NestedField(5, "low", DoubleType(), required=False),
    NestedField(6, "close", DoubleType(), required=False),
    NestedField(7, "volume", DoubleType(), required=False),
    NestedField(8, "vwap", DoubleType(), required=False),
    NestedField(9, "trade_count", LongType(), required=False),
    NestedField(10, "adj_factor", DoubleType(), required=False),
    NestedField(11, "source", StringType(), required=False),
    identifier_field_ids=[1, 2],
)

_ARROW_SCHEMA = pa.schema([
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
    ("open", pa.float64()), ("high", pa.float64()),
    ("low", pa.float64()), ("close", pa.float64()),
    ("volume", pa.float64()), ("vwap", pa.float64()),
    ("trade_count", pa.int64()), ("adj_factor", pa.float64()),
    ("source", pa.string()),
])


def _ts(d, h=14, mi=30):
    return datetime(2024, 1, d, h, mi, tzinfo=UTC)


def _rows(triples, src):
    """triples: (symbol, day, close) → an Arrow table for one source."""
    n = len(triples)
    return pa.table({
        "symbol": [s for s, _, _ in triples],
        "timestamp": pa.array([_ts(d) for _, d, _ in triples], type=pa.timestamp("us", tz="UTC")),
        "open": [c - 0.5 for _, _, c in triples],
        "high": [c + 1.0 for _, _, c in triples],
        "low": [c - 1.0 for _, _, c in triples],
        "close": [float(c) for _, _, c in triples],
        "volume": [1000.0 + i for i in range(n)],
        "vwap": [float(c) for _, _, c in triples],
        "trade_count": [10 + i for i in range(n)],
        "adj_factor": [1.0] * n,
        "source": [src] * n,
    }, schema=_ARROW_SCHEMA)


class _FakeCatalog:
    """Maps a SourceSpec.table_id → a loaded pyiceberg Table."""
    def __init__(self, tables):
        self._tables = tables

    def load_table(self, table_id):
        if table_id not in self._tables:
            raise FileNotFoundError(table_id)
        return self._tables[table_id]


@pytest.fixture
def lake(tmp_path):
    """Local Iceberg catalog with polygon + schwab tables (overlap+gap)."""
    wh = tmp_path / "wh"
    wh.mkdir()
    cat = SqlCatalog(
        "test",
        uri=f"sqlite:///{tmp_path}/cat.db",
        warehouse=f"file://{wh}",
    )
    cat.create_namespace("equities")
    poly = cat.create_table("equities.polygon_adjusted", schema=_SCHEMA)
    schwab = cat.create_table("equities.schwab_universe", schema=_SCHEMA)
    # AAPL: poly d1,d2,d3 ; schwab d2,d3,d4 → d2,d3 contested (poly wins), d4 gap-only
    # MSFT: poly d1     ; schwab d2        → disjoint
    poly.append(_rows([("AAPL", 1, 100), ("AAPL", 2, 101), ("AAPL", 3, 102),
                       ("MSFT", 1, 200)], "polygon-adjusted"))
    schwab.append(_rows([("AAPL", 2, 901), ("AAPL", 3, 902), ("AAPL", 4, 903),
                         ("MSFT", 2, 999)], "schwab"))

    specs = [
        SourceSpec("polygon_adjusted", "equities.polygon_adjusted", 2, "computed", True, "polygon-adjusted"),
        SourceSpec("schwab_universe", "equities.schwab_universe", 1, "pass_through", False, "schwab"),
    ]
    return _FakeCatalog({s.table_id: t for s, t in
                         zip(specs, [poly, schwab])}), specs


def _read(lake, **kw):
    cat, specs = lake
    # monkeypatch resolve to use our specs unless caller selects a subset
    sel = kw.pop("sources", None)
    chosen = resolve_sources(sel, available=specs)
    return ra.read_arrow(catalog=cat, sources=[s.name for s in chosen], **kw)


def test_union_dedup_precedence_and_gap(lake, monkeypatch):
    monkeypatch.setattr(ra, "resolve_sources",
                        lambda sel=None, available=None: resolve_sources(sel, available=lake[1]))
    out = ra.read_arrow("AAPL", _ts(1), _ts(10), catalog=lake[0])
    df = pl.from_arrow(out).sort("timestamp")
    closes = dict(zip(df["timestamp"].to_list(), df["close"].to_list()))
    assert df.height == 4
    assert closes[_ts(2)] == 101.0  # polygon wins contested
    assert closes[_ts(3)] == 102.0
    assert closes[_ts(4)] == 903.0  # schwab gap-only survives


def test_single_source_selection(lake, monkeypatch):
    monkeypatch.setattr(ra, "resolve_sources",
                        lambda sel=None, available=None: resolve_sources(sel, available=lake[1]))
    out = ra.read_arrow("AAPL", _ts(1), _ts(10), sources=["polygon_adjusted"], catalog=lake[0])
    df = pl.from_arrow(out).sort("timestamp")
    assert df.height == 3  # only polygon d1,d2,d3
    assert df["close"].to_list() == [100.0, 101.0, 102.0]


def test_projection_pushdown_columns(lake, monkeypatch):
    monkeypatch.setattr(ra, "resolve_sources",
                        lambda sel=None, available=None: resolve_sources(sel, available=lake[1]))
    out = ra.read_arrow("AAPL", _ts(1), _ts(10), columns=["close"], catalog=lake[0])
    assert out.column_names == ["close"]
    assert out.num_rows == 4


def test_cold_start_missing_source_degrades(lake, monkeypatch):
    monkeypatch.setattr(ra, "resolve_sources",
                        lambda sel=None, available=None: resolve_sources(sel, available=lake[1]))
    # Drop schwab from the catalog → union degrades to polygon, no raise.
    cat = _FakeCatalog({"equities.polygon_adjusted": lake[0]._tables["equities.polygon_adjusted"]})
    out = ra.read_arrow("AAPL", _ts(1), _ts(10), catalog=cat)
    df = pl.from_arrow(out)
    assert df.height == 3  # polygon only


def test_resolve_sources_unknown_raises():
    with pytest.raises(ValueError):
        resolve_sources(["nope"], available=resolve_sources(None, available=[
            SourceSpec("polygon_adjusted", "x", 2, "computed", True, "p")]))
