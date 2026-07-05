"""
ThetaData EOD options history backfill (GEX chapter, 2026-07).

Pulls per-contract EOD greeks + open interest for the options universe
from a LOCAL Theta Terminal and appends to the options lake:

  /v3/option/history/greeks/eod    -> options.thetadata_greeks_eod
  /v3/option/history/open_interest -> options.thetadata_oi_eod

Terminal setup (once):
  1. download https://download-unstable.thetadata.us/ThetaTerminalv3.jar
  2. echo 'THETA_DATA_API_KEY="<key>"' > .env  (next to the jar)
  3. java -Xms2G -Xmx4G -jar ThetaTerminalv3.jar   # serves 127.0.0.1:25503

Design (docs/standards/coding.md — no silent failures):
  - work unit = (endpoint, symbol, month); each unit gets an S3 completion
    marker (thetadata_backfill_markers/<endpoint>/<symbol>/<YYYY-MM>.json)
    written AFTER the lake append commits — reruns skip completed units,
    so bronze append-only stays idempotent without hot-path deletes.
  - every unit logs an outcome (rows appended, empty, failed); the run
    exits non-zero if any unit failed and prints the failed list.
  - 4 workers to match the Standard tier's concurrency cap.
  - --probe prints raw response columns for one symbol-day and exits —
    run it first; column mapping is validated loudly on every chunk.

  poetry run python scripts/thetadata_backfill.py --probe SPY
  poetry run python scripts/thetadata_backfill.py               # full pull
  poetry run python scripts/thetadata_backfill.py --symbols SPY QQQ --start 2016-01
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import pyarrow as pa  # noqa: E402
import requests  # noqa: E402

import app.config  # noqa: F401,E402  (AWS env normalization)
from app.config import settings  # noqa: E402

BASE = "http://127.0.0.1:25503"
MARKER_PREFIX = "thetadata_backfill_markers"
FIRST_MONTH = "2016-01"  # Standard tier first access date
WORKERS = 4

GREEKS_ARROW = pa.schema([
    pa.field("underlying_symbol", pa.string(), nullable=False),
    pa.field("eod_date", pa.date32(), nullable=False),
    pa.field("expiration_date", pa.date32(), nullable=False),
    pa.field("strike", pa.float64(), nullable=False),
    pa.field("put_call", pa.string(), nullable=False),
    pa.field("underlying_price", pa.float64(), nullable=True),
    pa.field("implied_vol", pa.float64(), nullable=True),
    pa.field("delta", pa.float64(), nullable=True),
    pa.field("gamma", pa.float64(), nullable=True),
    pa.field("theta", pa.float64(), nullable=True),
    pa.field("vega", pa.float64(), nullable=True),
    pa.field("vanna", pa.float64(), nullable=True),
    pa.field("charm", pa.float64(), nullable=True),
    pa.field("bid", pa.float64(), nullable=True),
    pa.field("ask", pa.float64(), nullable=True),
    pa.field("close", pa.float64(), nullable=True),
    pa.field("volume", pa.int64(), nullable=True),
    pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("ingestion_run_id", pa.string(), nullable=False),
])

OI_ARROW = pa.schema([
    pa.field("underlying_symbol", pa.string(), nullable=False),
    pa.field("eod_date", pa.date32(), nullable=False),
    pa.field("expiration_date", pa.date32(), nullable=False),
    pa.field("strike", pa.float64(), nullable=False),
    pa.field("put_call", pa.string(), nullable=False),
    pa.field("open_interest", pa.int64(), nullable=False),
    pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("ingestion_run_id", pa.string(), nullable=False),
])

# response-column candidates (validated per chunk; --probe to inspect)
_COL_CANDIDATES: dict[str, list[str]] = {
    "expiration": ["expiration", "expiration_date"],
    "strike": ["strike"],
    "right": ["right", "put_call"],
    "date": ["timestamp", "date", "created"],
    "underlying_price": ["underlying_price", "underlying", "underlying_last"],
    "implied_vol": ["implied_vol", "iv", "implied_volatility"],
    "open_interest": ["open_interest", "oi"],
}


def _pick(df: pd.DataFrame, key: str, required: bool = True) -> str | None:
    for cand in _COL_CANDIDATES.get(key, [key]):
        if cand in df.columns:
            return cand
    if required:
        raise RuntimeError(
            f"response is missing a {key!r} column — got {list(df.columns)}; "
            "run --probe and update _COL_CANDIDATES")
    return None


def _fetch_csv(path: str, params: dict) -> pd.DataFrame:
    """GET with Next-Page pagination; empty frame on the no-data status."""
    frames: list[pd.DataFrame] = []
    url, page_params = f"{BASE}{path}", {**params, "format": "csv"}
    for attempt in range(4):
        try:
            while True:
                resp = requests.get(url, params=page_params, timeout=300)
                if resp.status_code == 472:  # theta: no data for the request
                    return pd.DataFrame()
                resp.raise_for_status()
                if resp.text.strip():
                    frames.append(pd.read_csv(io.StringIO(resp.text)))
                nxt = resp.headers.get("Next-Page")
                if not nxt or nxt.lower() == "null":
                    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                url, page_params = nxt, {}
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and status < 500 and status != 429:
                raise  # 4xx (other than 429) will not heal — surface it
            if attempt == 3:
                raise
            wait = 5 * (attempt + 1)
            print(f"  retry {attempt + 1}/3 after {type(e).__name__} ({status}) in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _norm_right(s: pd.Series) -> pd.Series:
    m = s.astype(str).str.upper().str[0].map({"C": "CALL", "P": "PUT"})
    if m.isna().any():
        raise RuntimeError(f"unparseable right values: {s[m.isna()].unique()[:5]}")
    return m


def _norm_strike(s: pd.Series) -> pd.Series:
    strike = pd.to_numeric(s, errors="raise").astype(float)
    if strike.median() > 20000:  # legacy 1/10-cent units, not dollars
        print("  NOTE: strikes look like 1/10-cent units — dividing by 1000", flush=True)
        strike = strike / 1000.0
    return strike


def _norm_date(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):  # YYYYMMDD ints
        return pd.to_datetime(s.astype(int).astype(str), format="%Y%m%d").dt.date
    return pd.to_datetime(s, errors="raise").dt.date


def _greeks_frame(df: pd.DataFrame, symbol: str, run_id: str) -> pa.Table:
    now = datetime.now(timezone.utc)
    out = pd.DataFrame({
        "underlying_symbol": symbol,
        "eod_date": _norm_date(df[_pick(df, "date")]),
        "expiration_date": _norm_date(df[_pick(df, "expiration")]),
        "strike": _norm_strike(df[_pick(df, "strike")]),
        "put_call": _norm_right(df[_pick(df, "right")]),
    })
    for field, key in (
        ("underlying_price", "underlying_price"), ("implied_vol", "implied_vol"),
        ("delta", "delta"), ("gamma", "gamma"), ("theta", "theta"), ("vega", "vega"),
        ("vanna", "vanna"), ("charm", "charm"), ("bid", "bid"), ("ask", "ask"),
        ("close", "close"),
    ):
        col = _pick(df, key, required=key in ("underlying_price", "gamma"))
        out[field] = pd.to_numeric(df[col], errors="coerce") if col else None
    vol_col = "volume" if "volume" in df.columns else None
    out["volume"] = pd.to_numeric(df[vol_col], errors="coerce").astype("Int64") if vol_col else None
    out["ingestion_ts"] = now
    out["ingestion_run_id"] = run_id
    return pa.Table.from_pandas(out, schema=GREEKS_ARROW, preserve_index=False)


def _oi_frame(df: pd.DataFrame, symbol: str, run_id: str) -> pa.Table:
    now = datetime.now(timezone.utc)
    out = pd.DataFrame({
        "underlying_symbol": symbol,
        "eod_date": _norm_date(df[_pick(df, "date")]),
        "expiration_date": _norm_date(df[_pick(df, "expiration")]),
        "strike": _norm_strike(df[_pick(df, "strike")]),
        "put_call": _norm_right(df[_pick(df, "right")]),
        "open_interest": pd.to_numeric(df[_pick(df, "open_interest")], errors="raise").astype("int64"),
    })
    out["ingestion_ts"] = now
    out["ingestion_run_id"] = run_id
    return pa.Table.from_pandas(out, schema=OI_ARROW, preserve_index=False)


ENDPOINTS = {
    "greeks": ("/v3/option/history/greeks/eod", _greeks_frame, GREEKS_ARROW),
    "oi": ("/v3/option/history/open_interest", _oi_frame, OI_ARROW),
}


def _months(start: str, end: str) -> list[tuple[str, str, str]]:
    """[(label, start_date, end_date)] month windows, inclusive."""
    return [(str(p), str(p.start_time.date()), str(p.end_time.date()))
            for p in pd.period_range(start, end, freq="M")]


def _s3():
    import boto3
    return boto3.client("s3")


def _marker_key(endpoint: str, symbol: str, month: str) -> str:
    return f"{MARKER_PREFIX}/{endpoint}/{symbol}/{month}.json"


def _completed_units(s3, bucket: str) -> set[tuple[str, str, str]]:
    done: set[tuple[str, str, str]] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=MARKER_PREFIX + "/"):
        for obj in page.get("Contents", []):
            parts = obj["Key"].split("/")  # prefix/endpoint/symbol/month.json
            if len(parts) == 4 and parts[3].endswith(".json"):
                done.add((parts[1], parts[2], parts[3][:-5]))
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="default: OPTIONS_SNAPSHOT_SYMBOLS from settings")
    ap.add_argument("--start", default=FIRST_MONTH, help="YYYY-MM (default 2016-01)")
    ap.add_argument("--end", default=None, help="YYYY-MM (default: current month)")
    ap.add_argument("--endpoints", nargs="*", default=list(ENDPOINTS),
                    choices=list(ENDPOINTS))
    ap.add_argument("--probe", metavar="SYMBOL",
                    help="print raw response columns for one recent day and exit")
    a = ap.parse_args()

    try:
        requests.get(f"{BASE}/v3/stock/list/symbols", params={"format": "csv"}, timeout=10)
    except requests.ConnectionError:
        raise SystemExit(
            f"Theta Terminal is not reachable at {BASE} — start it first "
            "(java -jar ThetaTerminalv3.jar with THETA_DATA_API_KEY in .env)")

    if a.probe:
        for name, (path, _, _) in ENDPOINTS.items():
            df = _fetch_csv(path, {
                "symbol": a.probe, "expiration": "*",
                "start_date": "2026-06-25", "end_date": "2026-06-26",
            })
            print(f"\n[{name}] {path} rows={len(df)}")
            print(f"  columns: {list(df.columns)}")
            if not df.empty:
                print(df.head(3).to_string())
        return 0

    symbols = a.symbols or sorted(
        s.strip().upper() for s in settings.options_snapshot_symbols.split(",") if s.strip())
    if not symbols or symbols == ["ACTIVE"]:
        raise SystemExit("no symbols — set OPTIONS_SNAPSHOT_SYMBOLS or pass --symbols")
    end_month = a.end or str(pd.Period(date.today(), freq="M"))
    months = _months(a.start, end_month)
    run_id = f"thetadata-backfill-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"

    from app.services.options.tables import (
        ensure_theta_greeks_eod,
        ensure_theta_oi_eod,
    )
    tables = {"greeks": ensure_theta_greeks_eod(), "oi": ensure_theta_oi_eod()}
    s3, bucket = _s3(), settings.stock_lake_bucket
    done = _completed_units(s3, bucket)

    units = [(ep, sym, m) for ep in a.endpoints for sym in symbols for m in months
             if (ep, sym, m[0]) not in done]
    print(f"plan: {len(units)} units ({len(symbols)} symbols x {len(months)} months x "
          f"{len(a.endpoints)} endpoints; {len(done)} already complete) run={run_id}", flush=True)

    failures: list[tuple[str, str, str, str]] = []
    appended = {ep: 0 for ep in ENDPOINTS}

    def _one(ep: str, sym: str, month: tuple[str, str, str]) -> tuple[str, int]:
        label, mstart, mend = month
        path, to_arrow, _ = ENDPOINTS[ep]
        # expiration=* is served day-at-a-time (provider constraint) —
        # fetch each weekday, append ONCE per unit so the marker stays atomic.
        frames = []
        for day in pd.bdate_range(mstart, mend):
            d = str(day.date())
            df = _fetch_csv(path, {"symbol": sym, "expiration": "*",
                                   "start_date": d, "end_date": d})
            if not df.empty:
                frames.append(df)
        rows = 0
        if frames:
            arrow = to_arrow(pd.concat(frames, ignore_index=True), sym, run_id)
            rows = arrow.num_rows
            tables[ep].append(arrow)
        s3.put_object(Bucket=bucket, Key=_marker_key(ep, sym, label),
                      Body=json.dumps({"rows": rows, "run_id": run_id,
                                       "completed_at": datetime.now(timezone.utc).isoformat()}))
        return label, rows

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(_one, ep, sym, m): (ep, sym, m[0]) for ep, sym, m in units}
        for i, fut in enumerate(as_completed(futs), 1):
            ep, sym, label = futs[fut]
            try:
                _, rows = fut.result()
                appended[ep] += rows
                print(f"[{i}/{len(units)}] {ep} {sym} {label}: {rows} rows "
                      f"({time.time() - t0:.0f}s elapsed)", flush=True)
            except Exception as e:  # noqa: BLE001 — collected and re-raised at exit
                failures.append((ep, sym, label, str(e)))
                print(f"[{i}/{len(units)}] {ep} {sym} {label}: FAILED {e}", flush=True)

    print(f"\nappended: {appended} | failures: {len(failures)}", flush=True)
    if failures:
        for f in failures[:20]:
            print(f"  FAILED {f}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
