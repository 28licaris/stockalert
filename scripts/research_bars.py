"""
Research bar snapshots: one parquet file feeding every MCPT runner, so
studies run identically on this machine (ClickHouse) or on cloud workers
(S3 object, no database) — selected purely by configuration.

  # export once (needs ClickHouse; s3:// needs AWS_PROFILE=stock-lake)
  poetry run python scripts/research_bars.py export \
      --config configs/dyn_breakout_v2_top50_brake.yaml \
      --start 2006-01-01 --end 2026-06-30 \
      --out s3://<bucket>/research/mcpt/universe_2006_2026.parquet

  # then any runner, anywhere:
  poetry run python scripts/mcpt_walkforward.py --config ... \
      --bars s3://<bucket>/research/mcpt/universe_2006_2026.parquet ...

Schema: symbol (str), timestamp (tz-aware), open/high/low/close/volume
(float64), sorted by (symbol, timestamp). Snapshots are immutable study
inputs — re-export rather than edit, and name them by universe + range.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from app.services.sim.permutation import PermutedBar  # noqa: E402

UTC = timezone.utc
COLUMNS = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]


def _is_s3(path: str) -> bool:
    return str(path).startswith("s3://")


def _split_s3(uri: str) -> tuple[str, str]:
    rest = uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"malformed s3 uri: {uri}")
    return bucket, key


def _s3_client():
    import boto3

    import app.config  # noqa: F401 — import side effect: _normalize_aws_env
    # (a blank ambient AWS_PROFILE hangs every AWS call ~45-90s)
    return boto3.client("s3")


def read_snapshot(source: str) -> pd.DataFrame:
    """Load a bar snapshot from a local path or s3:// uri. Validates schema."""
    if _is_s3(source):
        bucket, key = _split_s3(source)
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            _s3_client().download_file(bucket, key, tmp.name)
            df = pd.read_parquet(tmp.name)
    else:
        df = pd.read_parquet(source)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"bar snapshot {source} missing columns {missing}")
    return df


def write_snapshot(df: pd.DataFrame, dest: str) -> None:
    if _is_s3(dest):
        bucket, key = _split_s3(dest)
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            df.to_parquet(tmp.name, index=False)
            _s3_client().upload_file(tmp.name, bucket, key)
    else:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(dest, index=False)


def _utc(bound) -> pd.Timestamp:
    t = pd.Timestamp(bound)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _filter(df: pd.DataFrame, symbols, start, end) -> pd.DataFrame:
    if symbols is not None:
        df = df[df["symbol"].isin(set(symbols))]
    ts = pd.to_datetime(df["timestamp"], utc=True)
    mask = pd.Series(True, index=df.index)
    if start is not None:
        mask &= ts >= _utc(start)
    if end is not None:
        mask &= ts <= _utc(end)
    return df[mask]


def load_frames(
    source: str, symbols=None, start=None, end=None, min_bars: int = 30,
) -> dict[str, pd.DataFrame]:
    """Snapshot -> Tier-1 shape: dict[symbol -> OHLCV DataFrame indexed by timestamp]."""
    df = _filter(read_snapshot(source), symbols, start, end)
    frames: dict[str, pd.DataFrame] = {}
    dropped = 0
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("timestamp").set_index(
            pd.DatetimeIndex(pd.to_datetime(g["timestamp"], utc=True))
        )[["open", "high", "low", "close", "volume"]].astype(float)
        if len(g) < min_bars or (g[["open", "high", "low", "close"]] <= 0).any().any():
            dropped += 1
            continue
        frames[sym] = g
    print(f"snapshot {source}: {len(frames)} symbols "
          f"({dropped} dropped: <{min_bars} bars or bad prices), "
          f"{sum(len(f) for f in frames.values()):,} bars", flush=True)
    if not frames:
        raise SystemExit(f"no usable symbols in {source} after filtering")
    return frames


def load_bar_lists(
    source: str, symbols=None, start=None, end=None,
) -> dict[str, list[PermutedBar]]:
    """Snapshot -> Tier-2 shape: dict[symbol -> list[Bar]] (PermutedBar satisfies the
    sim Bar Protocol; here it just carries real values)."""
    frames = load_frames(source, symbols, start, end, min_bars=1)
    out: dict[str, list[PermutedBar]] = {}
    for sym, df in frames.items():
        out[sym] = [
            PermutedBar(sym, ts.to_pydatetime(), o, h, l, c, v)
            for ts, o, h, l, c, v in zip(
                df.index, df["open"].tolist(), df["high"].tolist(),
                df["low"].tolist(), df["close"].tolist(), df["volume"].tolist())
        ]
    return out


def export(symbols: list[str], start: str, end: str, dest: str,
           table: str = "ohlcv_daily") -> None:
    """ClickHouse bar table -> snapshot parquet at dest (local or s3://)."""
    from app.db.client import get_client

    rows = get_client().query(
        "SELECT symbol, timestamp, open, high, low, close, volume "
        f"FROM {table} FINAL "
        "WHERE symbol IN %(symbols)s AND toDate(timestamp) BETWEEN %(start)s AND %(end)s "
        "ORDER BY symbol, timestamp",
        parameters={"symbols": symbols, "start": start, "end": end},
    ).result_rows
    if not rows:
        raise SystemExit(f"no ohlcv_daily rows for {len(symbols)} symbols in {start}..{end}")
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    for c in COLUMNS[2:]:
        df[c] = df[c].astype(float)
    write_snapshot(df, dest)
    print(f"exported {len(df):,} bars / {df['symbol'].nunique()} symbols "
          f"{start}..{end} -> {dest}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="ClickHouse -> snapshot parquet")
    ex.add_argument("--config", help="portfolio YAML supplying `symbols`")
    ex.add_argument("--symbols", nargs="*")
    ex.add_argument("--start", required=True)
    ex.add_argument("--end", required=True)
    ex.add_argument("--out", required=True, help="local path or s3://bucket/key")
    ex.add_argument("--table", default="ohlcv_daily")
    a = ap.parse_args(argv)
    if a.symbols:
        symbols = a.symbols
    elif a.config:
        symbols = yaml.safe_load(Path(a.config).read_text())["symbols"]
    else:
        raise SystemExit("supply --symbols or --config")
    export(symbols, a.start, a.end, a.out, table=a.table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
