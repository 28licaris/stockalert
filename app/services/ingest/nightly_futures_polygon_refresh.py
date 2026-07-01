"""Nightly Polygon flat-file → deep-history futures refresh.

Keeps the Polygon-sourced deep history fresh (the authoritative back-adjusted
continuous series), complementing nightly_futures_refresh (Schwab → the recent
~48-day live tip). Each run, for every CME session day not yet in
``futures.polygon_raw``:

  1. mirror that day's minute+session flat files → ``polygon_flatfiles_mirror/``
     (idempotent byte copy; reuses the mirror's copy_one),
  2. parse the day's minute file → append outright contracts to
     ``futures.polygon_raw`` (reuses parse_mirror_file),
  3. rebuild each active root's ``futures.polygon_continuous`` from polygon_raw
     and replace it atomically (volume roll + ratio back-adjustment must
     re-scale history at rolls, so a per-root rebuild is the robust choice).

Polygon finalizes a day's flat files ~11:00 ET the next morning, so the default
run hour (afternoon ET) safely has yesterday ready.

Gating: ``FUTURES_POLYGON_NIGHTLY_ENABLED`` + lake bucket + Polygon flat-file
S3 creds. Heavy sync work runs in a thread so the event loop stays free.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

# Large PyIceberg multipart writes (the per-root continuous rebuild) need the
# AWS auto-checksum default reverted — see reference_s3_crc64nvme_multipart.
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")

from app.config import settings

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
FUTURES_POLYGON_NIGHTLY_DEFAULT_HOUR_UTC = 21  # ~4pm/5pm ET; yesterday is final
_EXCHANGES = ["us_futures_cme", "us_futures_cbot", "us_futures_comex", "us_futures_nymex"]
_DATASETS = ["minute_aggs_v1", "session_aggs_v1"]
_MIRROR_PREFIX = "polygon_flatfiles_mirror"


def _gated() -> tuple[bool, str]:
    if not getattr(settings, "futures_polygon_nightly_enabled", False):
        return True, "FUTURES_POLYGON_NIGHTLY_ENABLED=false"
    if not (settings.stock_lake_bucket or "").strip():
        return True, "STOCK_LAKE_BUCKET is empty"
    if not (settings.polygon_s3_access_key_id and settings.polygon_s3_secret_access_key):
        return True, "Polygon flat-file S3 creds missing"
    return False, ""


def _seconds_until_next_run(hour_utc: int, *, now: datetime | None = None) -> float:
    from datetime import timedelta
    now = now or datetime.now(timezone.utc)
    h = max(0, min(23, int(hour_utc)))
    target = now.replace(hour=h, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _mirror_day(src, dst, d: date) -> int:
    """Byte-mirror one day's minute+session files for all exchanges. Returns
    files copied (skips holidays/404 and already-present files)."""
    from botocore.exceptions import ClientError

    from scripts.polygon_futures_mirror import copy_one

    src_bucket = settings.polygon_s3_bucket
    dst_bucket = settings.stock_lake_bucket
    copied = 0
    for ex in _EXCHANGES:
        for ds in _DATASETS:
            key = f"{ex}/{ds}/{d:%Y}/{d:%m}/{d:%Y-%m-%d}.csv.gz"
            try:
                head = src.head_object(Bucket=src_bucket, Key=key)
            except ClientError:
                continue  # weekend / holiday / not yet published
            size = head["ContentLength"]
            dest_key = f"{_MIRROR_PREFIX}/{key}"
            try:
                dh = dst.head_object(Bucket=dst_bucket, Key=dest_key)
                if dh["ContentLength"] == size:
                    continue  # already mirrored
            except ClientError:
                pass
            copy_one(src, dst, src_bucket, dst_bucket, _MIRROR_PREFIX, key, size)
            copied += 1
    return copied


def _parse_day(s3, d: date) -> int:
    """Parse one day's mirrored minute files → append to polygon_raw."""
    from scripts.polygon_futures_parse_raw import parse_mirror_file

    from app.services.futures.polygon_raw_sink import PolygonRawFuturesSink

    bucket = settings.stock_lake_bucket
    sink = PolygonRawFuturesSink()
    written = 0
    for ex in _EXCHANGES:
        key = f"{_MIRROR_PREFIX}/{ex}/minute_aggs_v1/{d:%Y}/{d:%m}/{d:%Y-%m-%d}.csv.gz"
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except Exception:
            continue
        df = parse_mirror_file(s3, bucket, key, ex)
        if len(df):
            written += sink.write_frame(df)
    return written


def _rebuild_continuous(roots: list[str], hysteresis_days: int) -> dict:
    """Rebuild each root's continuous series from polygon_raw, replacing it
    atomically. Returns {root: rows}."""
    from pyiceberg.expressions import EqualTo

    from scripts.polygon_futures_build_continuous import build_continuous_frame

    from app.services.futures.polygon_continuous_sink import PolygonContinuousSink
    from app.services.futures.tables import ensure_polygon_raw
    from app.services.iceberg_catalog import get_catalog

    raw = ensure_polygon_raw(get_catalog())
    sink = PolygonContinuousSink()
    cols = ("contract", "timestamp", "open", "high", "low", "close",
            "volume", "vwap", "trade_count")
    out: dict = {}
    for root in roots:
        df = raw.scan(row_filter=EqualTo("root", root),
                      selected_fields=cols).to_arrow().to_pandas()
        adj, _ = build_continuous_frame(df, root, hysteresis_days)
        if adj is None or adj.empty:
            out[root] = 0
            continue
        out[root] = sink.replace_symbol(adj, "/" + root)
        logger.info("nightly_futures_polygon: rebuilt /%s (%d bars)", root, out[root])
    return out


def _continuous_last(cont, symbol: str):
    """Latest (timestamp, contract) for a continuous root, scanning only the
    recent window (cheap). None if the root has no recent continuous bars."""
    from datetime import timedelta

    from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual

    since = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    df = cont.scan(
        row_filter=And(EqualTo("symbol", symbol), GreaterThanOrEqual("timestamp", since)),
        selected_fields=("timestamp", "contract"),
    ).to_arrow().to_pandas()
    if df.empty:
        return None
    row = df.loc[df["timestamp"].idxmax()]
    return row["timestamp"], row["contract"]


def _refresh_continuous_incremental(roots: list[str], hysteresis_days: int) -> dict:
    """Per root: APPEND the new front-month bars when nothing rolled, and only
    FULL-REBUILD the roots whose front contract actually changed.

    Detection: the dominant-volume contract for each new ET day. If every new
    day's dominant == the root's current front contract, no roll happened →
    append those bars at adj_factor=1.0 (the front segment is always 1.0).
    Otherwise a roll (or thereabouts) occurred → rebuild that root so the
    back-adjustment is re-scaled correctly. Conservative: when in doubt it
    rebuilds, which is always correct.
    """
    import pandas as pd
    from pyiceberg.expressions import And, EqualTo, GreaterThan

    from scripts.polygon_futures_build_continuous import build_continuous_frame

    from app.services.futures.polygon_continuous_sink import PolygonContinuousSink
    from app.services.futures.tables import ensure_polygon_continuous, ensure_polygon_raw
    from app.services.iceberg_catalog import get_catalog

    raw = ensure_polygon_raw(get_catalog())
    cont = ensure_polygon_continuous(get_catalog())
    sink = PolygonContinuousSink()
    cols = ("contract", "timestamp", "open", "high", "low", "close",
            "volume", "vwap", "trade_count")

    appended: dict = {}
    rebuilt: dict = {}
    for root in roots:
        sym = "/" + root
        last = _continuous_last(cont, sym)
        if last is None:
            # No recent continuous data → first build / cold root.
            df = raw.scan(row_filter=EqualTo("root", root), selected_fields=cols).to_arrow().to_pandas()
            adj, _ = build_continuous_frame(df, root, hysteresis_days)
            if adj is not None and not adj.empty:
                rebuilt[root] = sink.replace_symbol(adj, sym)
                logger.info("nightly_futures_polygon: built /%s cold (%d bars)", root, rebuilt[root])
            continue

        last_ts, last_contract = last
        new = raw.scan(
            row_filter=And(EqualTo("root", root), GreaterThan("timestamp", last_ts.isoformat())),
            selected_fields=cols,
        ).to_arrow().to_pandas()
        if new.empty:
            continue  # this root already current

        new = new.drop_duplicates(subset=["contract", "timestamp"], keep="last")
        new["etdate"] = new["timestamp"].dt.tz_convert(NY).dt.date
        vol = new.groupby(["etdate", "contract"])["volume"].sum().reset_index()
        dominant = vol.loc[vol.groupby("etdate")["volume"].idxmax()]

        if (dominant["contract"] == last_contract).all():
            # No roll — append the front contract's new bars at adj_factor 1.0.
            app = new[new["contract"] == last_contract].copy()
            app["symbol"] = sym
            app["adj_factor"] = 1.0
            n = sink.write_frame(app)
            appended[root] = n
            logger.info("nightly_futures_polygon: appended /%s (%d bars, no roll)", root, n)
        else:
            df = raw.scan(row_filter=EqualTo("root", root), selected_fields=cols).to_arrow().to_pandas()
            adj, _ = build_continuous_frame(df, root, hysteresis_days)
            rebuilt[root] = sink.replace_symbol(adj, sym)
            logger.info("nightly_futures_polygon: rebuilt /%s on roll (%d bars)", root, rebuilt[root])

    return {"appended": appended, "rebuilt": rebuilt}


def _refresh_sync(target: date | None, roots: list[str] | None, hysteresis_days: int) -> dict:
    import boto3
    from botocore.config import Config

    from app.services.futures.gaps import (
        is_futures_session_day,
        missing_futures_sessions,
        yesterday_et,
    )
    from app.services.futures.tables import ensure_polygon_raw
    from app.services.iceberg_catalog import get_catalog
    from scripts.polygon_futures_build_continuous import DEFAULT_ROOTS

    roots = roots or DEFAULT_ROOTS
    cfg = Config(retries={"max_attempts": 10, "mode": "adaptive"}, max_pool_connections=32)
    src = boto3.client("s3", endpoint_url=settings.polygon_s3_endpoint,
                       aws_access_key_id=settings.polygon_s3_access_key_id,
                       aws_secret_access_key=settings.polygon_s3_secret_access_key,
                       region_name="us-east-1", config=cfg)
    dst = boto3.client("s3", region_name=settings.stock_lake_region, config=cfg)

    raw = ensure_polygon_raw(get_catalog())
    if target is not None:
        days = [target] if is_futures_session_day(target) else []
    else:
        days = missing_futures_sessions(raw, through=yesterday_et())
    if not days:
        logger.info("nightly_futures_polygon: polygon_raw up to date — nothing to do")
        return {"skipped": True, "reason": "no missing days"}

    logger.info("nightly_futures_polygon: %d day(s) to ingest: %s",
                len(days), [d.isoformat() for d in days])
    mirrored = parsed = 0
    for d in days:
        mirrored += _mirror_day(src, dst, d)
        parsed += _parse_day(dst, d)

    res = _refresh_continuous_incremental(roots, hysteresis_days)
    return {
        "days": [d.isoformat() for d in days],
        "files_mirrored": mirrored,
        "raw_rows_appended": parsed,
        "roots_appended": len(res["appended"]),
        "roots_rebuilt": len(res["rebuilt"]),
        "continuous_rows_appended": sum(res["appended"].values()),
        "continuous_rows_rebuilt": sum(res["rebuilt"].values()),
    }


async def refresh_futures_polygon_yesterday(
    *, target: date | None = None, roots: list[str] | None = None,
    hysteresis_days: int = 3,
) -> dict:
    """Ingest missing day(s) into polygon_raw and rebuild continuous. Heavy
    sync work runs in a thread. ``target`` forces a single date (CLI/tests)."""
    gated, why = _gated()
    if gated:
        logger.info("nightly_futures_polygon: skipping — %s", why)
        return {"skipped": True, "reason": why}
    return await asyncio.to_thread(_refresh_sync, target, roots, hysteresis_days)


async def run_futures_polygon_refresh_loop() -> None:
    gated, why = _gated()
    if gated:
        logger.info("nightly_futures_polygon: loop not started — %s", why)
        return
    hour = int(getattr(settings, "futures_polygon_nightly_run_hour_utc",
                       FUTURES_POLYGON_NIGHTLY_DEFAULT_HOUR_UTC))
    logger.info("nightly_futures_polygon: loop armed (run hour %02d:00 UTC)", hour)
    while True:
        try:
            wait_s = _seconds_until_next_run(hour)
            logger.info("nightly_futures_polygon: sleeping %.0fs until next run", wait_s)
            await asyncio.sleep(wait_s)
            from app.services.jobs.service import audit_run
            async with audit_run("nightly_futures_polygon_refresh") as rec:
                rec.result = await refresh_futures_polygon_yesterday()
        except asyncio.CancelledError:
            logger.info("nightly_futures_polygon: loop cancelled")
            raise
        except Exception as e:
            logger.exception("nightly_futures_polygon: unexpected error: %s", e)
            await asyncio.sleep(300)
