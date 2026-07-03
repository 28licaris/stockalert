"""
Tier-2 walk-forward MCPT: full-engine permutation test.

Runs the REAL portfolio backtest (same engine, config, risk caps, and
dynamic momentum selection as production research), then re-runs it on
N bar-permuted universes (shared master shuffle; benchmark permuted
too, so regime/RS filters see no real data). The MCPT p-value is the
probability the portfolio result arises from structureless data with
the same return distribution — the final statistical gate before a
candidate may be paper-traded (docs/standards/trading_subsystem.md).

`--start-after` keeps bars at/before that date REAL (walk-forward
style: warmup/train prefix real, evaluation window permuted).

Resumable: results append to a JSONL; re-running with the same --out
skips already-completed permutation indices.

Usage:
  poetry run python scripts/mcpt_walkforward.py --config configs/dyn_breakout_v2_top50_brake.yaml \
      --start 2006-01-01T00:00:00Z --end 2021-12-31T23:59:59Z --n-perms 200
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from app.services.sim.backtester import Backtester  # noqa: E402
from app.services.sim.market_context import MarketContext  # noqa: E402
from app.services.sim.permutation import permute_bar_lists  # noqa: E402
from app.services.sim.schemas import BacktestConfig  # noqa: E402
from app.services.sim.significance import mcpt_pvalue  # noqa: E402
from scripts.run_backtest import _load_strategy  # noqa: E402

UTC = timezone.utc
METRICS = ("sharpe_ratio", "total_return", "profit_factor", "max_drawdown")


def _build_cfg(r: dict, start: str | None, end: str | None) -> BacktestConfig:
    return BacktestConfig(
        symbols=r["symbols"], start=start or r["start"], end=end or r["end"],
        interval=r.get("interval", "1d"), starting_cash=float(r.get("starting_cash", 100_000)),
        history_window=int(r.get("history_window", 250)), benchmark=r.get("benchmark"),
        max_concurrent_positions=int(r.get("max_concurrent_positions", 10)),
        max_portfolio_heat=float(r.get("max_portfolio_heat", 0.10)),
        momentum_top_n=r.get("momentum_top_n"),
        momentum_bottom_n=r.get("momentum_bottom_n"),
        momentum_lookback=int(r.get("momentum_lookback", 60)),
        daily_table=r.get("daily_table"),
        hourly_table=r.get("hourly_table"),
        ranked_admission=bool(r.get("ranked_admission", False)),
        dd_brake_limit=r.get("dd_brake_limit"),
        dd_brake_floor=float(r.get("dd_brake_floor", 0.0)),
    )


def _metrics_of(res) -> dict:
    m = res.metrics
    return {k: getattr(m, k) for k in METRICS}


def _bench_context(cfg: BacktestConfig, bars_by_symbol) -> MarketContext:
    bars = bars_by_symbol.get(cfg.benchmark, [])
    if not bars:
        raise SystemExit(
            f"benchmark {cfg.benchmark} not in the loaded universe — it must be "
            "permuted with everything else; add it to the config symbols")
    close = pd.Series([b.close for b in bars],
                      index=pd.DatetimeIndex([b.timestamp for b in bars]))
    return MarketContext(cfg.benchmark, close)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, help="portfolio YAML (run_portfolio format)")
    ap.add_argument("--start", default=None, help="override config start")
    ap.add_argument("--end", default=None, help="override config end")
    ap.add_argument("--start-after", default=None,
                    help="ISO date: bars at/before stay REAL (walk-forward prefix)")
    ap.add_argument("--n-perms", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--metric", default="sharpe_ratio", choices=METRICS,
                    help="primary metric for the p-value (all four are recorded)")
    ap.add_argument("--bars", default=None,
                    help="bar snapshot (local path or s3://) from research_bars.py; "
                         "when set, no ClickHouse needed — cloud-worker mode")
    ap.add_argument("--out", default=None,
                    help="JSONL results path (default data/mcpt/; s3:// supported — "
                         "shard syncs to S3 after every permutation)")
    a = ap.parse_args(argv)

    r = yaml.safe_load(Path(a.config).read_text())
    cfg = _build_cfg(r, a.start, a.end)
    start_after = datetime.fromisoformat(a.start_after) if a.start_after else None
    if start_after and start_after.tzinfo is None:
        start_after = start_after.replace(tzinfo=UTC)

    s3_sink = None
    if a.out and a.out.startswith("s3://"):
        import tempfile

        from scripts.research_bars import _s3_client, _split_s3
        bucket, key = _split_s3(a.out)
        s3 = _s3_client()
        out = Path(tempfile.gettempdir()) / f"mcpt_shard_{Path(key).name}"
        try:
            s3.download_file(bucket, str(key), str(out))
            print(f"resuming from {a.out}", flush=True)
        except Exception as exc:  # botocore ClientError — 404 means fresh shard
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchKey"):
                raise
        s3_sink = (s3, bucket, key)
    else:
        out = Path(a.out) if a.out else Path("data/mcpt") / (
            f"wf_{Path(a.config).stem}_{str(cfg.start)[:10]}_{str(cfg.end)[:10]}.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)

    def _sync() -> None:
        if s3_sink:
            s3_sink[0].upload_file(str(out), s3_sink[1], s3_sink[2])

    done: dict[int, dict] = {}
    real_row = None
    if out.exists():
        for line in out.read_text().splitlines():
            row = json.loads(line)
            if row["perm"] == -1:
                real_row = row
            else:
                done[row["perm"]] = row
        print(f"resuming: {len(done)} permutations already in {out}", flush=True)

    bt = Backtester()
    if a.bars:
        from scripts.research_bars import load_bar_lists
        full = {cfg.interval: load_bar_lists(
            a.bars, symbols=cfg.symbols, start=cfg.start, end=cfg.end)}
    else:
        print(f"loading {len(cfg.symbols)} symbols from ClickHouse (once)…", flush=True)
        full = bt._fetch_bars_multi(cfg, [cfg.interval])
    n_bars = sum(len(v) for v in full[cfg.interval].values())
    print(f"  loaded {n_bars:,} bars across "
          f"{sum(1 for v in full[cfg.interval].values() if v)} populated symbols", flush=True)
    bt._capture_snapshot = lambda *args, **kw: None  # type: ignore[assignment]

    def _run(bars_by_symbol) -> dict:
        bt._fetch_bars_multi = lambda *args, **kw: {cfg.interval: bars_by_symbol}  # type: ignore[assignment]
        bt._load_benchmark = (  # type: ignore[assignment]
            (lambda config, interval: _bench_context(cfg, bars_by_symbol))
            if cfg.benchmark else (lambda config, interval: None))
        strat = _load_strategy(r["strategy"], r.get("strategy_params", {}), interval=cfg.interval)
        return _metrics_of(bt.run_portfolio(strat, cfg))

    if real_row is None:
        t0 = time.time()
        real = _run(full[cfg.interval])
        real_row = {"perm": -1, "seed": None, "metrics": real,
                    "elapsed_s": round(time.time() - t0, 1)}
        with out.open("a") as fh:
            fh.write(json.dumps(real_row) + "\n")
        _sync()
        print(f"REAL run ({real_row['elapsed_s']}s): "
              + "  ".join(f"{k}={v if v is not None else float('nan'):.4f}"
                          for k, v in real.items()),
              flush=True)
        print(f"  estimated MCPT total ≈ "
              f"{(real_row['elapsed_s'] * 1.3) * a.n_perms / 3600:.1f} h "
              f"for {a.n_perms} permutations (permute+run)", flush=True)
    else:
        print(f"REAL run (from resume file): {real_row['metrics']}", flush=True)

    real_metric = real_row["metrics"][a.metric]
    if real_metric is None:
        raise SystemExit(f"real run produced no {a.metric} — nothing to test")

    for i in range(a.n_perms):
        if i in done:
            continue
        t0 = time.time()
        seed_i = a.seed + i
        perm_bars = permute_bar_lists(full[cfg.interval], seed=seed_i,
                                      start_after=start_after)
        metrics = _run(perm_bars)
        row = {"perm": i, "seed": seed_i, "metrics": metrics,
               "elapsed_s": round(time.time() - t0, 1)}
        done[i] = row
        with out.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        _sync()
        vals = [d["metrics"][a.metric] for d in done.values()
                if d["metrics"][a.metric] is not None]
        n_asgood = sum(1 for v in vals if v >= real_metric)
        print(f"  perm {i + 1:>4}/{a.n_perms}  {a.metric}="
              f"{metrics[a.metric] if metrics[a.metric] is not None else float('nan'):.4f}  "
              f"({row['elapsed_s']}s)  running p={(1 + n_asgood) / (1 + len(vals)):.4f}",
              flush=True)

    vals = [d["metrics"][a.metric] for d in done.values()]
    dropped = sum(1 for v in vals if v is None)
    if dropped:
        print(f"  NOTE: {dropped} permutations produced no {a.metric} (excluded from null)")
    res = mcpt_pvalue(real_metric, [v for v in vals if v is not None],
                      greater_is_better=(a.metric != "max_drawdown"))
    print(f"\nWALK-FORWARD MCPT [{Path(a.config).stem}] {str(cfg.start)[:10]}..{str(cfg.end)[:10]}"
          f" metric={a.metric} start_after={a.start_after}")
    print(f"  {res.summary()}")
    print(f"  results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
