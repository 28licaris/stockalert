"""
read_arrow — the fast, modular cold read path for adjusted OHLCV.

Spec: docs/lake_read_layer_design.md §3.2. Engine chosen by the spike
in `scripts/spikes/` and the in-region benchmark (§6): **Polars over
PyIceberg** — 14× faster than the Python baseline and the lowest peak
memory of any engine tested, while reusing the PyIceberg planning layer
already proven in production here (Glue catalog, merge-on-read deletes).

What this is for (the COLD path; the hot/interactive path stays on
ClickHouse and never calls this):
  - sync / one-time-load ClickHouse from the lake,
  - batch pull for backtesting + agent training.

Design levers baked in from day one:
  - **Lazy + pushdown (lever 3):** `scan()` returns a Polars
    `LazyFrame`. Column projection and the symbol/window predicate push
    *into* the Iceberg/parquet scan, so a consumer that only wants
    `close` never reads `open/high/low/vwap` off S3. `read_arrow()` is a
    thin `.collect()` over it.
  - **Merge-aware sort (lever 2):** sources carry an Iceberg sort order
    on (symbol, timestamp); on the single-symbol path we tell Polars the
    timestamp column is pre-sorted so the union is a cheap merge rather
    than a full re-sort.
  - **Streaming engine:** the union/dedup/sort runs in Polars'
    out-of-core streaming engine so whole-market batches (hundreds of
    GB — single-node territory, not Spark) don't blow up memory.

Modularity comes from `source_registry.SourceSpec` — adding a provider
is one registry entry, no edit here.

NOT here (separate, signoff-gated follow-ups): a materialized union
table (dual storage), a result cache, and post-union gap-fill.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Sequence

import pyarrow as pa

from app.services.readers.source_registry import SourceSpec, resolve_sources

logger = logging.getLogger(__name__)

# Canonical projection — schema-parity columns shared by every equity
# source (02_schema.md "schema parity payoff"). Dedup keys first.
CANON_COLUMNS = [
    "symbol",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "trade_count",
    "adj_factor",
    "source",
]
_KEY_COLUMNS = ["symbol", "timestamp"]
_PREC = "__prec"


def scan(
    symbols: Sequence[str] | str,
    start: datetime,
    end: datetime,
    *,
    sources: Optional[Sequence[str]] = None,
    columns: Optional[Sequence[str]] = None,
    catalog=None,
):
    """Build the lazy cold-read query over the selected lake sources.

    Returns a Polars `LazyFrame` — nothing is read until you `.collect()`
    (or call `read_arrow`). This is the lever-3 seam: a caller can chain
    its own `.filter(...)`/`.select(...)`/`.group_by(...)` and Polars
    pushes that down into the parquet scan.

    Args:
      symbols: one ticker or a list. Case-insensitive; whitespace
        trimmed. Empty → a LazyFrame that collects to zero rows.
      start, end: half-open window `[start, end)`. Any tz; coerced to
        UTC at the boundary.
      sources: subset of registry source names (e.g. `["polygon_adjusted"]`
        to read polygon deep history alone). None → the full union,
        polygon winning contested (symbol, timestamp).
      columns: subset of `CANON_COLUMNS` to project. None → all. The
        dedup keys + precedence are always read internally and the
        result is restricted to `columns` at the end.
      catalog: PyIceberg catalog override (tests). None → `get_catalog()`.

    Cold-start safe: a source whose Iceberg table doesn't exist yet is
    skipped with a warning, never raised — the union degrades to the
    sources that do exist (mirrors AdjustedOhlcvReader's behaviour).
    """
    import polars as pl

    syms = _normalize_symbols(symbols)
    start_utc, end_utc = _coerce_utc(start), _coerce_utc(end)
    specs = resolve_sources(sources)
    single_symbol = len(syms) == 1

    # Always carry keys + precedence internally; restrict to `columns` last.
    want_cols = list(columns) if columns else list(CANON_COLUMNS)
    read_cols = list(dict.fromkeys([*_KEY_COLUMNS, *want_cols]))

    frames: list[pl.LazyFrame] = []
    for spec in specs:
        lf = _scan_source(
            spec, syms, start_utc, end_utc,
            read_cols=read_cols, single_symbol=single_symbol,
            catalog=catalog, pl=pl,
        )
        if lf is not None:
            frames.append(lf)

    if not frames:
        # No source contributed (cold start / all missing) → typed empty.
        return pl.DataFrame(schema={c: _polars_dtype(c, pl) for c in read_cols}).lazy()

    unioned = _union_dedup(frames, pl=pl)
    # Restrict to the caller's projection (keys re-added if they asked).
    final_cols = [c for c in want_cols if c in read_cols] or want_cols
    return unioned.select(final_cols)


def read_arrow(
    symbols: Sequence[str] | str,
    start: datetime,
    end: datetime,
    *,
    sources: Optional[Sequence[str]] = None,
    columns: Optional[Sequence[str]] = None,
    catalog=None,
) -> pa.Table:
    """Materialize the cold read as a single Arrow table (no per-row objects).

    Thin `.collect()` over `scan(...)` using Polars' streaming engine.
    This is the bulk path for ML/backtest pulls and the CH cold-loader.
    """
    lf = scan(symbols, start, end, sources=sources, columns=columns, catalog=catalog)
    return _collect(lf).to_arrow()


# ─────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────

def _scan_source(spec: SourceSpec, syms, start_utc, end_utc, *,
                 read_cols, single_symbol, catalog, pl):
    """Lazy per-source scan with predicate + projection pushdown.

    Returns a LazyFrame tagged with `__prec`, or None if the source's
    table can't be loaded (cold-start safe).
    """
    table = _load_table(spec, catalog)
    if table is None:
        return None

    have = set(table.schema().column_names)
    proj = [c for c in read_cols if c in have]

    try:
        lf = pl.scan_iceberg(table)
    except Exception as e:  # noqa: BLE001 — boundary; report, don't swallow
        logger.warning("read_arrow: pl.scan_iceberg(%s) failed: %s; "
                       "skipping source", spec.table_id, e)
        return None

    lf = lf.filter(
        pl.col("symbol").is_in(syms)
        & (pl.col("timestamp") >= start_utc)
        & (pl.col("timestamp") < end_utc)
    ).select(proj)

    # Backfill any projected column the source lacks, so the union is
    # schema-aligned (parity means this is normally a no-op).
    missing = [c for c in read_cols if c not in have]
    if missing:
        lf = lf.with_columns([pl.lit(None).alias(c) for c in missing])
    lf = lf.with_columns(pl.lit(spec.precedence, dtype=pl.Int32).alias(_PREC))

    # Lever 2: on the single-symbol path a (symbol, timestamp)-sorted
    # table is sorted by timestamp within the symbol, so declare it —
    # Polars then merges rather than re-sorts. Safe only for one symbol
    # (across symbols the global ts order is not monotonic).
    if single_symbol and spec.sorted_by_ts:
        lf = lf.set_sorted("timestamp")
    return lf


def _union_dedup(frames, *, pl):
    """Concat all sources → keep highest-precedence row per (symbol, ts)
    → sort ascending. Identical contract to the legacy Python union, but
    in Polars' columnar/streaming engine."""
    combined = pl.concat(frames, how="vertical_relaxed")
    return (
        combined
        .sort([*_KEY_COLUMNS, _PREC], descending=[False, False, True])
        .unique(subset=_KEY_COLUMNS, keep="first", maintain_order=True)
        .drop(_PREC)
    )


def _collect(lf):
    """Collect with the streaming engine; fall back loudly if a Polars
    version rejects the kwarg (never silently use a different engine)."""
    try:
        return lf.collect(engine="streaming")
    except TypeError:
        logger.info("read_arrow: this Polars lacks engine=streaming; "
                    "using default collect()")
        return lf.collect()


def _load_table(spec: SourceSpec, catalog):
    """Load a source's PyIceberg table; None if missing (cold start)."""
    try:
        if catalog is None:
            from app.services.iceberg_catalog import get_catalog
            catalog = get_catalog()
        return catalog.load_table(spec.table_id)
    except Exception as e:  # noqa: BLE001 — boundary
        logger.warning("read_arrow: source %s (%s) not loadable (%s); "
                       "treating as empty for this read",
                       spec.name, spec.table_id, e)
        return None


def _normalize_symbols(symbols) -> list[str]:
    if isinstance(symbols, str):
        symbols = [symbols]
    out, seen = [], set()
    for s in symbols or []:
        sym = (s or "").strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def _coerce_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _polars_dtype(col: str, pl):
    """Dtype for an empty-frame column so cold-start results are typed."""
    if col == "symbol" or col == "source":
        return pl.Utf8
    if col == "timestamp":
        return pl.Datetime(time_unit="us", time_zone="UTC")
    if col == "trade_count":
        return pl.Int64
    return pl.Float64
