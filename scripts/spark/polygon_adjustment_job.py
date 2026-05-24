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
    """Compute and write the adjusted bars. Returns (n_symbols, n_rows)."""
    spark = get_spark(JOB_NAME)
    from pyspark.sql import functions as F  # lazy import

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
            < F.lit(datetime.combine(until, datetime.min.time())).cast("timestamp") + F.expr("INTERVAL 1 DAY")
        )

    # Only forward-split rows contribute to adj_factor (factor != 1.0
    # ignores no-op rows; cash dividends use `cash_amount`, never
    # `factor`).
    splits = spark.sql(f"""
        SELECT symbol, ex_date, factor
        FROM {CORP_ACTIONS_TABLE}
        WHERE action_type = 'split'
          AND factor IS NOT NULL
          AND factor != 1.0
    """)
    if symbols:
        splits = splits.where(F.col("symbol").isin(symbols))

    # Left-join raw × splits on symbol, gate each split's log(factor)
    # contribution on ex_date > bar.date, sum-of-logs → exp gives the
    # cumulative future-splits factor. Rows with no future splits
    # produce NULL sum → exp NULL → coalesce(1.0).
    #
    # bar.date is computed once (cast timestamp → date) so the join
    # predicate has clean comparison semantics. v1's
    # cumulative_factor_after() uses bar_date (not bar_ts) for the
    # same reason — a bar ON the split day itself is in the post-split
    # frame.
    bar_date = F.to_date(raw["timestamp"]).alias("_bar_date")

    factored = (
        raw.withColumn("_bar_date", F.to_date(F.col("timestamp")))
        .join(splits, on="symbol", how="left")
        .groupBy(
            raw["symbol"], raw["timestamp"],
            raw["open"], raw["high"], raw["low"], raw["close"],
            raw["volume"], raw["vwap"], raw["trade_count"],
        )
        .agg(
            F.coalesce(
                F.exp(F.sum(
                    F.when(F.col("ex_date") > F.col("_bar_date"), F.log("factor"))
                )),
                F.lit(1.0),
            ).alias("adj_factor")
        )
    )

    # Apply the adjustment math + restamp ingestion columns + tag source.
    ingestion_run_id = str(uuid.uuid4())
    ingestion_ts = datetime.now(tz=timezone.utc).replace(microsecond=0)
    adjusted = factored.select(
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

    # Merge-on-read overwrite of touched partitions only. Whole-market
    # full rebuild rewrites every partition (~$2-3 on EMR Serverless).
    # Incremental --since runs touch only the partitions covered by
    # the filter — no full-table rewrite.
    adjusted.writeTo(ADJUSTED_TABLE).overwritePartitions()

    # Cheap to compute after the write because the writer's stats are
    # cached; if not, this is one extra Spark stage (still cheap).
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
