"""
Offline correctness gate for the lake-read engine spike.

The spec's Risk "Dedup correctness across engines" demands a
golden-fixture test asserting the Arrow path == the Python path BEFORE
any consumer cuts over. This runs the three compute engines (Python
baseline, Polars, DuckDB) over local parquet — no cloud, no Glue — and
asserts byte-identical dedup/sort output.

It exercises the SAME union/dedup/sort primitives the cloud bench uses,
so passing here means the engines agree on the contract; the cloud
bench then only has to measure speed, not re-litigate correctness.

Skips cleanly if duckdb/polars aren't installed (the `spike` group).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.spikes.lake_read_engines import (
    CANON_COLS,
    CANON_FIELDS,
    SourceSpec,
    _coerce,
    fingerprint,
    union_dedup_baseline,
    union_dedup_duckdb,
    union_dedup_polars,
)

pl = pytest.importorskip("polars")
duckdb = pytest.importorskip("duckdb")

UTC = timezone.utc


def _ts(y, mo, d, h=14, mi=30):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def _write_source(path, symbol_rows):
    """symbol_rows: list of (symbol, ts, close). Other cols derived."""
    n = len(symbol_rows)
    arrays = {
        "symbol": [s for s, _, _ in symbol_rows],
        "timestamp": pa.array([t for _, t, _ in symbol_rows], type=pa.timestamp("us", tz="UTC")),
        "open": [c - 0.5 for _, _, c in symbol_rows],
        "high": [c + 1.0 for _, _, c in symbol_rows],
        "low": [c - 1.0 for _, _, c in symbol_rows],
        "close": [float(c) for _, _, c in symbol_rows],
        "volume": [1000.0 + i for i in range(n)],
        "vwap": [float(c) for _, _, c in symbol_rows],
        "trade_count": [10 + i for i in range(n)],
        "adj_factor": [1.0] * n,
    }
    tbl = pa.table(arrays)
    pq.write_table(tbl, path)
    return tbl


@pytest.fixture
def two_sources(tmp_path):
    """Polygon (precedence 2) and Schwab (precedence 1) with overlap+gap.

    AAPL: polygon t1,t2,t3 ; schwab t2,t3,t4  → t2,t3 contested (poly wins)
    MSFT: polygon t1,t2    ; schwab t2,t3      → t2 contested (poly wins)
    """
    poly = tmp_path / "polygon.parquet"
    schwab = tmp_path / "schwab.parquet"
    _write_source(poly, [
        ("AAPL", _ts(2024, 1, 1), 100), ("AAPL", _ts(2024, 1, 2), 101), ("AAPL", _ts(2024, 1, 3), 102),
        ("MSFT", _ts(2024, 1, 1), 200), ("MSFT", _ts(2024, 1, 2), 201),
    ])
    _write_source(schwab, [
        ("AAPL", _ts(2024, 1, 2), 901), ("AAPL", _ts(2024, 1, 3), 902), ("AAPL", _ts(2024, 1, 4), 903),
        ("MSFT", _ts(2024, 1, 2), 999), ("MSFT", _ts(2024, 1, 3), 998),
    ])
    return {
        "polygon": (SourceSpec("polygon_adjusted", str(poly), precedence=2, bucketed=True), str(poly)),
        "schwab": (SourceSpec("schwab_universe", str(schwab), precedence=1, bucketed=False), str(schwab)),
    }


def _baseline(two):
    tables = []
    for spec, path in two.values():
        arrow = pq.read_table(path)
        tables.append((spec.precedence, _coerce(arrow, spec)))
    return union_dedup_baseline(tables)


def _polars(two):
    frames = []
    for spec, path in two.values():
        lf = (
            pl.scan_parquet(path)
            .with_columns(pl.lit(spec.name).alias("source"))
            .select(CANON_COLS)
        )
        frames.append((spec.precedence, lf))
    return union_dedup_polars(frames)


def _duckdb(two):
    con = duckdb.connect()
    parts = []
    for spec, path in two.values():
        cols = ", ".join(c for c, _ in CANON_FIELDS if c != "source")
        parts.append(
            f"SELECT {cols}, '{spec.name}' AS source, {spec.precedence} AS __prec "
            f"FROM read_parquet('{path}')"
        )
    return union_dedup_duckdb(con, parts)


def test_all_engines_agree(two_sources):
    base = _baseline(two_sources)
    pol = _polars(two_sources)
    duck = _duckdb(two_sources)

    fb, fp, fd = fingerprint(base), fingerprint(pol), fingerprint(duck)
    assert fb == fp, f"polars disagrees with baseline:\n  baseline={fb}\n  polars  ={fp}"
    assert fb == fd, f"duckdb disagrees with baseline:\n  baseline={fb}\n  duckdb  ={fd}"


def test_dedup_precedence_and_gap(two_sources):
    """Polygon must win contested timestamps; non-contested rows survive."""
    base = _baseline(two_sources).sort_by([("symbol", "ascending"), ("timestamp", "ascending")])
    rows = base.to_pylist()
    # 4 AAPL (t1..t4) + 3 MSFT (t1..t3) = 7 after dedup
    assert base.num_rows == 7, [r["symbol"] + str(r["timestamp"]) for r in rows]

    by_key = {(r["symbol"], r["timestamp"]): r for r in rows}
    # contested AAPL t2/t3 → polygon's 101/102, not schwab's 901/902
    assert by_key[("AAPL", _ts(2024, 1, 2))]["close"] == 101.0
    assert by_key[("AAPL", _ts(2024, 1, 3))]["close"] == 102.0
    # gap-only AAPL t4 → schwab's 903 survives
    assert by_key[("AAPL", _ts(2024, 1, 4))]["close"] == 903.0
    # contested MSFT t2 → polygon 201; schwab-only MSFT t3 → 998
    assert by_key[("MSFT", _ts(2024, 1, 2))]["close"] == 201.0
    assert by_key[("MSFT", _ts(2024, 1, 3))]["close"] == 998.0
    # winning source recorded
    assert by_key[("AAPL", _ts(2024, 1, 2))]["source"] == "polygon_adjusted"
    assert by_key[("AAPL", _ts(2024, 1, 4))]["source"] == "schwab_universe"
