"""
Lake read-layer engine spike — DuckDB vs Polars vs PyIceberg+Python.

Spec: docs/lake_read_layer_design.md (§3.2 "Columnar bulk path").

Goal of this spike: decide which engine backs `read_arrow()` for the
COLD read path — the lake-direct path used to (a) sync/one-time-load
ClickHouse, (b) batch-pull history for backtesting + agent training.
The hot/interactive path stays on ClickHouse and is out of scope.

This module is intentionally **self-contained and experimental** (it
lives under scripts/spikes/, not app/). When a winner is chosen it
graduates into `app/services/readers/read_arrow.py` as a real PR per
the spec's migration plan. Until then, nothing in app/ imports this.

Three engines, ONE union/dedup/sort contract so results are directly
comparable:

  - ``baseline``  PyIceberg → Arrow, then union/dedup/sort in **Python**
                  (mirrors today's AdjustedOhlcvReader.get_bars_union;
                  this is the bottleneck the spec is trying to remove).
  - ``polars``    Polars LazyFrame (pl.scan_iceberg / pl.scan_parquet);
                  union/dedup/sort in Rust, returns Arrow.
  - ``duckdb``    PyIceberg plans the file list; DuckDB read_parquet does
                  union/dedup/sort in C++ SQL, returns Arrow.
  - ``duckdb_iceberg``  DuckDB's native iceberg_scan over the Glue table's
                  metadata json — directly probes the spec's Risk #1
                  (DuckDB↔Glue Iceberg maturity). May fail; failure is
                  reported, never swallowed.

The dedup contract (identical across engines):
  union all sources → keep the highest-``precedence`` row per
  (symbol, timestamp) → sort by (symbol, timestamp) ascending.

Run one engine once and print a JSON result line (used by the bench
harness, which runs each engine in a fresh subprocess for clean peak
RSS):

    AWS_PROFILE=stock-lake poetry run python -m scripts.spikes.lake_read_engines \\
        --engine duckdb --symbols AAPL --start 2020-01-01 --end 2025-01-01
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import resource
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pyarrow as pa

logger = logging.getLogger("lake_read_engines")

# ── Canonical output schema ─────────────────────────────────────────
# Every engine must return exactly this, so a fingerprint comparison is
# apples-to-apples. `source_table` records which SourceSpec won the
# (symbol, timestamp) — useful for provenance, dropped from the dedup key.
CANON_FIELDS = [
    ("symbol", pa.string()),
    ("timestamp", pa.timestamp("us", tz="UTC")),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.float64()),
    ("vwap", pa.float64()),
    ("trade_count", pa.int64()),
    ("adj_factor", pa.float64()),
    ("source", pa.string()),
]
CANON_COLS = [name for name, _ in CANON_FIELDS]
CANON_SCHEMA = pa.schema(CANON_FIELDS)


@dataclass(frozen=True)
class SourceSpec:
    """One pluggable cold-path source. The spec's §3.1 registry entry.

    Adding a provider (Alpaca, …) is one of these — no merge-logic edit.
    """

    name: str           # logical name, e.g. "polygon_adjusted"
    table_id: str       # PyIceberg identifier "<glue_db>.<table>"
    precedence: int     # higher wins on (symbol, timestamp) dedup
    bucketed: bool      # planning hint: bucket(N,symbol)+month vs month-only


def default_equity_sources() -> list[SourceSpec]:
    """The two equities sources, polygon winning (mirrors today's union).

    Imports app settings lazily so the offline equality test (which
    constructs its own SourceSpecs over local parquet) never needs env.
    """
    from app.services.equities.schemas import equities_table_id

    return [
        SourceSpec("polygon_adjusted", equities_table_id("polygon_adjusted"), precedence=2, bucketed=True),
        SourceSpec("schwab_universe", equities_table_id("schwab_universe"), precedence=1, bucketed=False),
    ]


# ── Engine-agnostic union/dedup/sort primitives ─────────────────────
# Each takes inputs already scoped to (symbols, [start,end)) per source
# and returns CANON_SCHEMA Arrow. These are what the offline test and
# the cloud bench both exercise, so the compute path is identical in
# both settings.

def union_dedup_baseline(tables: list[tuple[int, pa.Table]]) -> pa.Table:
    """Python dict union/dedup + sorted() — the path the spec replaces.

    `tables` is [(precedence, arrow), …]. Higher precedence overwrites,
    so we apply low→high and let the last write win, matching
    AdjustedOhlcvReader.get_bars_union (schwab first, polygon last).
    """
    merged: dict[tuple, dict] = {}
    for _prec, arrow in sorted(tables, key=lambda t: t[0]):
        if arrow.num_rows == 0:
            continue
        for row in arrow.to_pylist():
            merged[(row["symbol"], row["timestamp"])] = row
    rows = sorted(merged.values(), key=lambda r: (r["symbol"], r["timestamp"]))
    return pa.Table.from_pylist(rows, schema=CANON_SCHEMA) if rows else CANON_SCHEMA.empty_table()


def union_dedup_polars(frames: list[tuple[int, "object"]]):
    """Polars: concat → tag precedence → sort → unique(first) → re-sort.

    `frames` is [(precedence, LazyFrame), …] already filtered to the
    request window with the canonical columns selected.
    """
    import polars as pl

    lazies = []
    for prec, lf in frames:
        lazies.append(lf.with_columns(pl.lit(prec, dtype=pl.Int32).alias("__prec")))
    if not lazies:
        return CANON_SCHEMA.empty_table()

    out = (
        pl.concat(lazies, how="vertical_relaxed")
        # highest precedence first so unique(keep="first") keeps the winner
        .sort(["symbol", "timestamp", "__prec"], descending=[False, False, True])
        .unique(subset=["symbol", "timestamp"], keep="first", maintain_order=True)
        .sort(["symbol", "timestamp"])
        .select(CANON_COLS)
        .collect()
    )
    return out.to_arrow().cast(CANON_SCHEMA)


_DUCKDB_DEDUP_SQL = """
WITH unioned AS (
{union_sql}
)
SELECT {cols}
FROM (
    SELECT *,
           row_number() OVER (
               PARTITION BY symbol, timestamp ORDER BY __prec DESC
           ) AS __rn
    FROM unioned
)
WHERE __rn = 1
ORDER BY symbol, timestamp
"""


def union_dedup_duckdb(con, parts: list[str]):
    """DuckDB: UNION ALL of pre-filtered subqueries → QUALIFY-style dedup.

    `parts` is a list of SQL SELECTs, each already projecting CANON_COLS
    plus a literal ``__prec`` and applying the symbol/window filter.
    """
    if not parts:
        return CANON_SCHEMA.empty_table()
    sql = _DUCKDB_DEDUP_SQL.format(
        union_sql="\n    UNION ALL\n".join(parts),
        cols=", ".join(CANON_COLS),
    )
    return con.execute(sql).to_arrow_table().cast(CANON_SCHEMA)


# ── Fingerprint (cross-engine correctness gate) ─────────────────────

def fingerprint(arrow: pa.Table) -> dict:
    """Deterministic summary of an output table for equality checks.

    Hashes the (symbol, timestamp, close, volume, adj_factor) tuples in
    canonical sort order. Two engines that agree on the dedup MUST
    produce the same fingerprint.
    """
    if arrow.num_rows == 0:
        return {"rows": 0, "hash": "empty"}
    arrow = arrow.sort_by([("symbol", "ascending"), ("timestamp", "ascending")])
    h = hashlib.sha256()
    cols = {c: arrow.column(c).to_pylist() for c in ("symbol", "timestamp", "close", "volume", "adj_factor")}
    for i in range(arrow.num_rows):
        ts = cols["timestamp"][i]
        h.update(
            f"{cols['symbol'][i]}|{ts.isoformat() if ts else ''}|"
            f"{_round(cols['close'][i])}|{_round(cols['volume'][i])}|"
            f"{_round(cols['adj_factor'][i])}\n".encode()
        )
    return {"rows": arrow.num_rows, "hash": h.hexdigest()[:16]}


def _round(v):
    return None if v is None else round(float(v), 6)


# ── Cloud wiring: build per-engine inputs from PyIceberg ─────────────

def _pyiceberg_filter(symbols: list[str], start: datetime, end: datetime):
    from pyiceberg.expressions import And, GreaterThanOrEqual, In, LessThan

    return And(
        In("symbol", symbols),
        And(GreaterThanOrEqual("timestamp", start), LessThan("timestamp", end)),
    )


def _load_tables(sources: list[SourceSpec]):
    """Load each source's PyIceberg Table once (cheap Glue metadata)."""
    from app.services.iceberg_catalog import get_catalog

    cat = get_catalog()
    out = []
    for s in sources:
        try:
            out.append((s, cat.load_table(s.table_id)))
        except Exception as e:  # noqa: BLE001 — boundary; report, don't swallow
            logger.warning("source %s (%s) not loadable: %s", s.name, s.table_id, e)
            out.append((s, None))
    return out


def run_cloud(engine: str, sources: list[SourceSpec], symbols: list[str],
              start: datetime, end: datetime) -> dict:
    """Execute one engine against the real lake; return a result dict."""
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    flt = _pyiceberg_filter(symbols, start, end)

    t0 = time.perf_counter()
    loaded = _load_tables(sources)
    plan_t0 = time.perf_counter()

    deletes_present = False
    arrow_out: pa.Table

    if engine == "baseline":
        tables = []
        for spec, tbl in loaded:
            if tbl is None:
                continue
            a = tbl.scan(row_filter=flt, selected_fields=tuple(CANON_COLS)).to_arrow()
            tables.append((spec.precedence, _coerce(a, spec)))
        plan_t1 = time.perf_counter()
        arrow_out = union_dedup_baseline(tables)

    elif engine == "polars":
        import polars as pl

        frames = []
        for spec, tbl in loaded:
            if tbl is None:
                continue
            lf = (
                pl.scan_iceberg(tbl)
                .filter(
                    pl.col("symbol").is_in(symbols)
                    & (pl.col("timestamp") >= start)
                    & (pl.col("timestamp") < end)
                )
                .with_columns(pl.lit(spec.name).alias("source"))
                .select(CANON_COLS)
            )
            frames.append((spec.precedence, lf))
        plan_t1 = time.perf_counter()
        arrow_out = union_dedup_polars(frames)

    elif engine == "duckdb":
        con = _duckdb_con()
        parts = []
        for spec, tbl in loaded:
            if tbl is None:
                continue
            files, has_deletes = _plan_files(tbl, flt)
            deletes_present = deletes_present or has_deletes
            if not files:
                continue
            file_list = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
            sym_list = ", ".join("'" + s + "'" for s in symbols)
            parts.append(
                f"SELECT symbol, timestamp, open, high, low, close, volume, vwap, "
                f"trade_count, adj_factor, '{spec.name}' AS source, {spec.precedence} AS __prec "
                f"FROM read_parquet([{file_list}]) "
                f"WHERE symbol IN ({sym_list}) "
                f"AND timestamp >= TIMESTAMPTZ '{start.isoformat()}' "
                f"AND timestamp <  TIMESTAMPTZ '{end.isoformat()}'"
            )
        plan_t1 = time.perf_counter()
        arrow_out = union_dedup_duckdb(con, parts)

    elif engine == "duckdb_iceberg":
        # Risk #1 probe: DuckDB's native iceberg_scan over Glue table
        # metadata. No fallback — if this can't read the table, that IS
        # the finding.
        con = _duckdb_con()
        con.execute("INSTALL iceberg; LOAD iceberg;")
        parts = []
        for spec, tbl in loaded:
            if tbl is None:
                continue
            meta = tbl.metadata_location
            sym_list = ", ".join("'" + s + "'" for s in symbols)
            parts.append(
                f"SELECT symbol, timestamp, open, high, low, close, volume, vwap, "
                f"trade_count, adj_factor, '{spec.name}' AS source, {spec.precedence} AS __prec "
                f"FROM iceberg_scan('{meta}', allow_moved_paths=true) "
                f"WHERE symbol IN ({sym_list}) "
                f"AND timestamp >= TIMESTAMPTZ '{start.isoformat()}' "
                f"AND timestamp <  TIMESTAMPTZ '{end.isoformat()}'"
            )
        plan_t1 = time.perf_counter()
        arrow_out = union_dedup_duckdb(con, parts)

    else:
        raise SystemExit(f"unknown engine: {engine}")

    done = time.perf_counter()
    fp = fingerprint(arrow_out)
    return {
        "engine": engine,
        "symbols": symbols,
        "rows": fp["rows"],
        "hash": fp["hash"],
        "planning_ms": round((plan_t1 - t0) * 1000, 1),
        "compute_ms": round((done - plan_t1) * 1000, 1),
        "total_ms": round((done - t0) * 1000, 1),
        "peak_rss_mb": _peak_rss_mb(),
        "deletes_present": deletes_present,
        "error": None,
    }


def _coerce(arrow: pa.Table, spec: SourceSpec) -> pa.Table:
    """Project a scanned Arrow table to CANON order; stamp source name."""
    have = set(arrow.schema.names)
    arrays, names = [], []
    for name, typ in CANON_FIELDS:
        if name == "source":
            arrays.append(pa.array([spec.name] * arrow.num_rows, type=pa.string()))
        elif name in have:
            arrays.append(arrow.column(name).cast(typ))
        else:
            arrays.append(pa.nulls(arrow.num_rows, type=typ))
        names.append(name)
    return pa.Table.from_arrays(arrays, names=names)


def _plan_files(table, flt):
    """Resolve concrete parquet paths for a pruned scan + flag deletes.

    DuckDB's read_parquet does NOT apply Iceberg merge-on-read delete
    files, so if any scan task carries deletes the duckdb-on-files
    result would be wrong. We surface that loudly rather than silently
    returning incorrect rows.
    """
    files, has_deletes = [], False
    for task in table.scan(row_filter=flt).plan_files():
        files.append(task.file.file_path)
        if getattr(task, "delete_files", None):
            has_deletes = True
    return files, has_deletes


def _duckdb_con():
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    # Use the AWS default credential chain (honours AWS_PROFILE=stock-lake).
    try:
        con.execute("CREATE SECRET lake (TYPE S3, PROVIDER credential_chain);")
    except Exception as e:  # noqa: BLE001
        logger.warning("duckdb credential_chain secret failed (%s); trying boto3 creds", e)
        _duckdb_creds_from_boto3(con)
    return con


def _duckdb_creds_from_boto3(con):
    import boto3

    from app.config import settings

    sess = boto3.Session(profile_name=settings.aws_profile or None)
    c = sess.get_credentials().get_frozen_credentials()
    con.execute(f"SET s3_region='{settings.stock_lake_region}';")
    con.execute(f"SET s3_access_key_id='{c.access_key}';")
    con.execute(f"SET s3_secret_access_key='{c.secret_key}';")
    if c.token:
        con.execute(f"SET s3_session_token='{c.token}';")


def _peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports kilobytes.
    div = 1024 * 1024 if platform.system() == "Darwin" else 1024
    return round(rss / div, 1)


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main(argv=None):
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Run one lake-read engine once; print JSON.")
    ap.add_argument("--engine", required=True,
                    choices=["baseline", "polars", "duckdb", "duckdb_iceberg"])
    ap.add_argument("--symbols", required=True, help="comma-separated tickers")
    ap.add_argument("--start", required=True, type=_parse_dt)
    ap.add_argument("--end", required=True, type=_parse_dt)
    ap.add_argument("--source-mode", default="union",
                    choices=["union", "polygon", "schwab"],
                    help="which source(s) to read; 'union' = both, polygon wins")
    args = ap.parse_args(argv)

    sources = default_equity_sources()
    if args.source_mode == "polygon":
        sources = [s for s in sources if s.name == "polygon_adjusted"]
    elif args.source_mode == "schwab":
        sources = [s for s in sources if s.name == "schwab_universe"]

    symbols = args.symbols.split(",")
    try:
        result = run_cloud(args.engine, sources, symbols, args.start, args.end)
    except Exception as e:  # noqa: BLE001 — top-level boundary: report as JSON
        logger.exception("engine %s failed", args.engine)
        result = {"engine": args.engine, "error": f"{type(e).__name__}: {e}",
                  "rows": None, "hash": None, "total_ms": None, "peak_rss_mb": _peak_rss_mb()}
    print(json.dumps(result))
    return 0 if result.get("error") is None else 1


if __name__ == "__main__":
    sys.exit(main())
