"""
Spark job — `equities.polygon_raw` + `equities.market_corp_actions`
→ `equities.polygon_adjusted`.

The cumulative future-splits factor for each bar:

    F(symbol, T) = ∏  factor_i  for each split with ex_date_i > date(T)
                 (returns 1.0 when no future splits exist)

Then for each raw bar:

    adj_open  = raw_open  / F
    adj_high  = raw_high  / F
    adj_low   = raw_low   / F
    adj_close = raw_close / F
    adj_volume = raw_volume × F      (preserves dollar volume)
    vwap and trade_count pass through unchanged
    adj_factor = F                   (stored so consumers can recover raw)

Identical math to v1's
`app.services.silver.ohlcv.normalize.cumulative_factor_after`,
implemented vectorized in Spark via `log → sum → exp` to handle
multiple future splits without overflow.

Invocation modes (docs/architecture_v2/07_runbook.md):

  - Local dev      → `STOCKALERT_SPARK_LOCAL_MODE=true python scripts/spark/polygon_adjustment_job.py --symbols AAPL --since 2024-01-01`
  - CodeBuild      → one-shot ops job
  - EMR Serverless → weekly cron after new corp_actions land

Cadence: weekly is sufficient because corp_actions arrive on a
business-day cadence and the adjustment math has no live-tier
latency requirement (the live tier reads from
`equities.schwab_universe` for "today" and `equities.polygon_adjusted`
only for cold-tier deep history).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.spark import get_spark, record_run  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("polygon_adjustment_job")


JOB_NAME = "polygon_adjustment_job"
RAW_TABLE = "lake.equities.polygon_raw"
ADJUSTED_TABLE = "lake.equities.polygon_adjusted"
CORP_ACTIONS_TABLE = "lake.equities.market_corp_actions"


def _parse_symbols(raw: str | None) -> list[str] | None:
    """Comma-separated symbols → upper-cased list; None / 'ALL' → None
    (whole-market). Empty entries are dropped so `--symbols ,AAPL,` is
    valid."""
    if not raw:
        return None
    if raw.strip().upper() == "ALL":
        return None
    out = [s.strip().upper() for s in raw.split(",") if s.strip()]
    return out or None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s)


def _ensure_target_exists() -> None:
    """Create `equities.polygon_adjusted` via PyIceberg if it doesn't
    exist yet (idempotent — calls into CV1's `ensure_polygon_adjusted`).

    Spark's writeTo + overwritePartitions can create the table itself
    in some configurations, but it picks default Iceberg properties
    that won't match our merge-on-read / file-size settings. Doing
    the create via PyIceberg makes the DDL match CV1 unconditionally.
    """
    from app.services.equities.tables import ensure_polygon_adjusted

    table = ensure_polygon_adjusted()
    log.info("verified target table exists: %s", table.name())


def adjust(
    symbols: list[str] | None,
    since: date | None,
    until: date | None,
) -> tuple[int, int]:
    """Compute and write the adjusted bars. Returns (n_symbols, n_rows).

    Algorithm (post-CV19 refactor — broadcast lookup, no shuffle):

      1. Pre-aggregate splits per (symbol, ex_date) and compute, per row,
         the cumulative factor for THIS ex_date plus all LATER ex_dates
         in the same symbol. (Tiny — ~3K rows total even whole-market.)
      2. Collect that splits_cum to the driver and build a per-symbol
         (ex_dates_asc, cum_factors) pair of NumPy arrays — `searchsorted`-
         ready.
      3. Broadcast the lookup dict to every executor.
      4. For each raw bar, a pandas_udf does an O(log(splits_per_symbol))
         binary search: find the smallest ex_date > bar_date → that row's
         cum_factor (else 1.0). No shuffle, no wide join, no groupBy.
      5. Apply adjustment math via column arithmetic and write.

    Why this beats the prior raw.join(splits).groupBy(9-cols) pattern:
      - That join inflated rows for symbols with multiple splits (AAPL
        bars × 2 splits = 2x; symbols with 5 splits = 5x). For
        whole-market with ~3K splits across the universe, this could
        materialize tens of billions of intermediate row-pairs.
      - The wide groupBy then shuffled all 2.1B rows by a 9-column hash,
        filling EMR executor disks (the "No space left on device" failure
        that motivated this refactor).
      - This version reads raw once, applies a tiny in-memory lookup per
        row, and writes once. Embarrassingly parallel.
    """
    import numpy as np
    import pandas as pd

    spark = get_spark(JOB_NAME)
    from pyspark.sql import Window, functions as F  # lazy import

    raw = spark.sql(f"SELECT * FROM {RAW_TABLE}")
    if symbols:
        raw = raw.where(F.col("symbol").isin(symbols))
    if since:
        raw = raw.where(F.col("timestamp") >= F.lit(since))
    if until:
        # Inclusive of `until` as a calendar day → cap at the start of
        # the NEXT day to keep the predicate timestamp-comparable.
        raw = raw.where(
            F.col("timestamp")
            < F.lit(datetime.combine(until, datetime.min.time())).cast("timestamp")
              + F.expr("INTERVAL 1 DAY")
        )

    # ─────────────────────────────────────────────────────────────────
    # Build splits_cum: per (symbol, ex_date), the cumulative factor for
    # THIS split and all LATER splits in the same symbol.
    #
    # AAPL has splits on 2014-06-09 (7×) and 2020-08-31 (4×). Result:
    #   (AAPL, 2020-08-31, 4)   ← only the 2020 split contributes
    #   (AAPL, 2014-06-09, 28)  ← 7 × 4 = 28 for bars before 2014-06-09
    # ─────────────────────────────────────────────────────────────────
    splits = spark.sql(f"""
        SELECT symbol, ex_date, factor
        FROM {CORP_ACTIONS_TABLE}
        WHERE action_type = 'split'
          AND factor IS NOT NULL
          AND factor != 1.0
    """)
    if symbols:
        splits = splits.where(F.col("symbol").isin(symbols))

    splits_agg = (
        splits
        .groupBy("symbol", "ex_date")
        .agg(F.sum(F.log("factor")).alias("lf"))
    )
    # Window: per symbol, DESC by ex_date, cumulative sum of lf for
    # current row + all preceding (= later in date) rows.
    # exp() of that sum = cumulative future-splits factor at this ex_date.
    w = (
        Window.partitionBy("symbol")
        .orderBy(F.desc("ex_date"))
        .rowsBetween(Window.unboundedPreceding, Window.currentRow)
    )
    splits_cum = (
        splits_agg
        .withColumn("cum_factor", F.exp(F.sum("lf").over(w)))
        .select("symbol", "ex_date", "cum_factor")
    )

    # ─────────────────────────────────────────────────────────────────
    # Collect to driver (tiny — ~3K rows whole-market) and build a
    # per-symbol (ex_dates_asc, cum_factors) lookup for binary search.
    # ─────────────────────────────────────────────────────────────────
    splits_pdf = splits_cum.toPandas()
    log.info(
        "splits_cum: %d (symbol, ex_date) rows for %d distinct symbols",
        len(splits_pdf), splits_pdf["symbol"].nunique() if len(splits_pdf) else 0,
    )

    lookup: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for sym, grp in splits_pdf.sort_values(["symbol", "ex_date"]).groupby("symbol"):
        # ex_date arrives as datetime64[ns] from PyIceberg/PyArrow → date promotion.
        # Keep as datetime64[ns] for fast searchsorted against bar timestamps.
        ex_dates_np = pd.to_datetime(grp["ex_date"]).to_numpy()
        cum_np = grp["cum_factor"].to_numpy(dtype=np.float64)
        lookup[sym] = (ex_dates_np, cum_np)

    lookup_bc = spark.sparkContext.broadcast(lookup)

    # ─────────────────────────────────────────────────────────────────
    # Per-row adj_factor via mapInPandas (explicit-schema variant —
    # avoids pandas_udf's type-annotation reflection, which collides
    # with this module's `from __future__ import annotations` and
    # fails with UNSUPPORTED_SIGNATURE on Spark 3.5).
    #
    # Operates per partition of raw — no shuffle, just a closure over
    # the broadcast variable. The output schema is raw + adj_factor.
    # ─────────────────────────────────────────────────────────────────
    from pyspark.sql.types import DoubleType, StructField, StructType

    # Defensive copy — raw.schema.add(...) MUTATES the underlying StructType
    # and returns it. Spark's lazy DataFrame plans share schema references,
    # so the mutation can leave the source `raw` DF with adj_factor visible
    # to the planner but not produced by the partition fn (depending on
    # eval order). UNRESOLVED_COLUMN at write time. Build a fresh StructType.
    out_schema = StructType(
        list(raw.schema.fields) + [StructField("adj_factor", DoubleType(), True)]
    )

    def add_adj_factor_partition(iterator):
        # Per-task local import keeps the closure pickle small and avoids
        # depending on numpy/pandas already being imported on the executor.
        import numpy as _np
        import pandas as _pd

        sd = lookup_bc.value
        for pdf in iterator:
            # Normalize bar timestamps to date-at-midnight for the search
            # (matches v1 cumulative_factor_after() semantics: bars ON
            # the split day are in the post-split frame; only ex_date >
            # date qualifies for adjustment).
            bar_dates = _pd.to_datetime(pdf["timestamp"]).dt.normalize().to_numpy()
            symbols_np = pdf["symbol"].to_numpy()
            out = _np.ones(len(pdf), dtype=_np.float64)
            for i in range(len(pdf)):
                entry = sd.get(symbols_np[i])
                if entry is None:
                    # Most symbols have zero splits in our window — common path.
                    continue
                ex_dates_np, cum = entry
                # side='right' → ex_date > bar_date (strict inequality).
                idx = int(_np.searchsorted(ex_dates_np, bar_dates[i], side="right"))
                if idx < len(ex_dates_np):
                    out[i] = cum[idx]
            pdf["adj_factor"] = out
            yield pdf

    raw_with_factor = raw.mapInPandas(add_adj_factor_partition, schema=out_schema)

    # ─────────────────────────────────────────────────────────────────
    # Apply adjustment math + restamp ingestion columns + tag source.
    # No shuffle: this is column arithmetic over the same partitioning
    # as raw_with_factor (which is raw's partitioning + the udf column).
    # ─────────────────────────────────────────────────────────────────
    ingestion_run_id = str(uuid.uuid4())
    ingestion_ts = datetime.now(tz=timezone.utc).replace(microsecond=0)
    adjusted = raw_with_factor.select(
        F.col("symbol"),
        F.col("timestamp"),
        (F.col("open") / F.col("adj_factor")).alias("open"),
        (F.col("high") / F.col("adj_factor")).alias("high"),
        (F.col("low") / F.col("adj_factor")).alias("low"),
        (F.col("close") / F.col("adj_factor")).alias("close"),
        (F.col("volume") * F.col("adj_factor")).alias("volume"),
        F.col("vwap"),
        F.col("trade_count"),
        F.lit("polygon-adjusted").alias("source"),
        F.lit(ingestion_ts).cast("timestamp").alias("ingestion_ts"),
        F.lit(ingestion_run_id).alias("ingestion_run_id"),
        F.col("adj_factor"),
    )

    # Cache before the write: writeTo + the two post-write counts each
    # trigger a separate evaluation of the upstream DAG. Without caching,
    # whole-market runs evaluate the 2.1B-row pipeline 3 times.
    adjusted = adjusted.cache()

    # Merge-on-read overwrite of touched partitions only. Whole-market
    # full rebuild rewrites every partition. Incremental --since runs
    # touch only the partitions covered by the filter — no full-table
    # rewrite.
    adjusted.writeTo(ADJUSTED_TABLE).overwritePartitions()

    n_symbols = adjusted.select("symbol").distinct().count()
    n_rows = adjusted.count()
    return n_symbols, n_rows


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbol list. Use 'ALL' or omit for whole-market.",
    )
    p.add_argument(
        "--since", default=None,
        help="Lower bound, YYYY-MM-DD. Omit for full history.",
    )
    p.add_argument(
        "--until", default=None,
        help="Upper bound (inclusive), YYYY-MM-DD. Omit for through-today.",
    )
    p.add_argument(
        "--skip-ensure", action="store_true",
        help=(
            "Skip the pyiceberg-backed ensure_polygon_adjusted() call. "
            "Required for EMR Serverless runs (pyiceberg is not in the EMR "
            "Python env). Caller is responsible for pre-creating the table "
            "locally before submitting the EMR job."
        ),
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    symbols = _parse_symbols(args.symbols)
    since = _parse_date(args.since)
    until = _parse_date(args.until)
    started = time.time()

    log.info(
        "polygon_adjustment_job: symbols=%s since=%s until=%s",
        symbols if symbols else "ALL", since, until,
    )

    if args.skip_ensure:
        log.info(
            "--skip-ensure: target table assumed to exist (caller-managed). "
            "Required for EMR Serverless (pyiceberg not in EMR env)."
        )
    else:
        try:
            _ensure_target_exists()
        except Exception as e:
            log.exception("failed to ensure target table exists: %s", e)
            record_run(
                job_name=JOB_NAME, status="error",
                started_at=started, error=f"ensure_target_failed: {e}",
            )
            return 1

    try:
        n_symbols, n_rows = adjust(symbols, since, until)
    except Exception as e:
        log.exception("adjustment Spark job failed: %s", e)
        record_run(
            job_name=JOB_NAME, status="error",
            started_at=started, error=str(e),
        )
        return 1

    record_run(
        job_name=JOB_NAME, status="ok",
        rows_written=n_rows, symbols_processed=n_symbols,
        started_at=started,
    )
    log.info("polygon_adjustment_job done: symbols=%d rows=%d", n_symbols, n_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
