#!/usr/bin/env python3
"""Build futures.polygon_continuous from futures.polygon_raw.

Phase 3 of the futures lake build — the derived continuous-root layer, analog
of equities.polygon_adjusted. For each root it:
  1. aggregates per-(ET-day, contract) volume from polygon_raw,
  2. picks the front-month contract per day (volume-based roll + hysteresis,
     volume_roll.front_month_schedule — zero REST, order from ticker codes),
  3. keeps that contract's bars (the stitched continuous series), and
  4. ratio back-adjusts so roll seams vanish; stores adj_factor (cumulative
     ratio; 1.0 on the front segment) so the true contract price is recoverable.

No silent failures: per-root bar/segment/roll accounting + a reconcile of rows
written vs table delta; non-zero exit on any error.

Usage:
    poetry run python scripts/polygon_futures_build_continuous.py --roots ES
    poetry run python scripts/polygon_futures_build_continuous.py   # all liquid roots
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

load_dotenv()
# See reference_s3_crc64nvme_multipart_badrequest — large PyIceberg writes.
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("polygon_futures_build_continuous")

NY = ZoneInfo("America/New_York")

# Default liquid roots to build continuous series for (the cockpit/backtest set).
DEFAULT_ROOTS = [
    "ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K",
    "GC", "MGC", "SI", "SIL", "HG", "PL", "PA",
    "CL", "MCL", "NG", "RB", "HO", "BZ",
    "ZB", "UB", "ZN", "ZF", "ZT", "ZC", "ZS", "ZW", "ZM", "ZL",
    "6E", "6J", "6B", "6A", "6C", "6S",
]


def build_continuous_frame(df_root: pd.DataFrame, root: str, hysteresis_days: int):
    """Return (adjusted_df, n_rolls) for one root, or (None, 0) if no data.

    df_root: all outright-contract bars for `root` (cols: contract, timestamp,
    open, high, low, close, volume, vwap, trade_count).
    """
    from app.services.futures.volume_roll import front_month_schedule, roll_days

    if df_root.empty:
        return None, 0
    df = df_root.sort_values("timestamp").reset_index(drop=True)
    df["etdate"] = df["timestamp"].dt.tz_convert(NY).dt.date

    # 1. per-(day, contract) volume → roll schedule
    vol = df.groupby(["etdate", "contract"])["volume"].sum()
    daily_volume: dict = {}
    for (d, c), v in vol.items():
        daily_volume.setdefault(d, {})[c] = float(v) if pd.notna(v) else 0.0
    schedule = front_month_schedule(daily_volume, hysteresis_days=hysteresis_days, root=root)
    if not schedule:
        return None, 0

    # 2. keep only the front-month contract's bars for each day
    df["front"] = df["etdate"].map(schedule)
    kept = df[df["contract"] == df["front"]].copy()
    if kept.empty:
        return None, 0
    kept = kept.sort_values("timestamp").reset_index(drop=True)

    # daily last close per contract (for roll-seam ratios), from the full root df
    last_close = (df.sort_values("timestamp")
                    .groupby(["etdate", "contract"])["close"].last())

    # 3. segments (consecutive same-contract runs) in the kept series
    seg_id = (kept["contract"] != kept["contract"].shift()).cumsum()
    segments = list(kept.groupby(seg_id, sort=True))
    rolls = roll_days(schedule)

    # 4. cumulative ratio factors, newest segment = 1.0, going backward
    factors: dict[int, float] = {}
    cum = 1.0
    for i in range(len(segments) - 1, -1, -1):
        factors[i] = cum
        if i > 0:
            old_seg = segments[i - 1][1]
            old_contract = old_seg["contract"].iloc[0]
            new_contract = segments[i][1]["contract"].iloc[0]
            seam_day = old_seg["etdate"].iloc[-1]      # old segment's last session
            p_old = last_close.get((seam_day, old_contract))
            p_new = last_close.get((seam_day, new_contract))
            if p_old and p_new and p_old > 0 and p_new > 0:
                cum = cum * (p_new / p_old)
            # else: keep cum unchanged (no clean overlap → no gap correction)

    # 5. apply factors
    out_frames = []
    for idx, (_, seg) in enumerate(segments):
        f = factors[idx]
        s = seg.copy()
        for col in ("open", "high", "low", "close", "vwap"):
            s[col] = s[col] * f
        s["adj_factor"] = f
        out_frames.append(s)
    adj = pd.concat(out_frames, ignore_index=True)
    adj["symbol"] = "/" + root
    return adj, len(rolls)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roots", nargs="+", default=DEFAULT_ROOTS)
    ap.add_argument("--hysteresis-days", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true", help="build + report, no write")
    args = ap.parse_args()

    from pyiceberg.expressions import EqualTo

    from app.services.futures.polygon_continuous_sink import PolygonContinuousSink
    from app.services.futures.tables import ensure_polygon_continuous, ensure_polygon_raw
    from app.services.iceberg_catalog import get_catalog

    raw = ensure_polygon_raw(get_catalog())
    cont = ensure_polygon_continuous(get_catalog())
    rows_before = _row_count(cont)
    logger.info("=== Build futures.polygon_continuous %s===",
                "| DRY RUN " if args.dry_run else "")
    logger.info("  roots: %s", " ".join(args.roots))
    logger.info("  hysteresis_days: %d", args.hysteresis_days)
    logger.info("  continuous rows before: %s", f"{rows_before:,}")

    sink = None if args.dry_run else PolygonContinuousSink()
    t0 = time.time()
    total_written = 0
    failures: list[tuple[str, str]] = []
    per_root: list[tuple[str, int, int]] = []

    cols = ("contract", "timestamp", "open", "high", "low", "close",
            "volume", "vwap", "trade_count")
    for root in args.roots:
        try:
            df = raw.scan(row_filter=EqualTo("root", root),
                          selected_fields=cols).to_arrow().to_pandas()
            adj, n_rolls = build_continuous_frame(df, root, args.hysteresis_days)
            if adj is None or adj.empty:
                logger.warning("[/%s] no continuous series (no data)", root)
                per_root.append((root, 0, 0))
                continue
            n = 0 if args.dry_run else sink.write_frame(adj)
            total_written += len(adj)
            per_root.append((root, len(adj), n_rolls))
            logger.info("[/%s] bars=%s rolls=%d span %s→%s (%.0fs)",
                        root, f"{len(adj):,}", n_rolls,
                        adj["timestamp"].min().date(), adj["timestamp"].max().date(),
                        time.time() - t0)
        except Exception as exc:
            failures.append((root, str(exc)))
            logger.error("[/%s] FAILED: %s", root, exc)

    logger.info("")
    logger.info("=== SUMMARY (%.0fs) ===", time.time() - t0)
    for root, bars, nrolls in per_root:
        logger.info("  /%-5s bars=%-12s rolls=%d", root, f"{bars:,}", nrolls)
    logger.info("  roots built: %d   rows: %s   failures: %d",
                len([p for p in per_root if p[1] > 0]), f"{total_written:,}", len(failures))

    if args.dry_run:
        logger.info("DRY RUN — no rows written.")
        return 1 if failures else 0

    cont.refresh()
    rows_after = _row_count(cont)
    delta = rows_after - rows_before
    logger.info("  table rows: before=%s after=%s delta=%s (expected +%s)",
                f"{rows_before:,}", f"{rows_after:,}", f"{delta:,}", f"{total_written:,}")
    if failures or delta != total_written:
        logger.error("BUILD INCOMPLETE — failures=%d, delta %s != written %s",
                     len(failures), f"{delta:,}", f"{total_written:,}")
        return 1
    logger.info("BUILD COMPLETE — %s continuous rows across %d roots.",
                f"{total_written:,}", len([p for p in per_root if p[1] > 0]))
    return 0


def _row_count(table) -> int:
    try:
        snap = table.current_snapshot()
        return int(snap.summary.additional_properties.get("total-records", 0)) if snap else 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
