"""
Run the PPL PIT calibration harness (spec P3) against the research bar tables.

This is the gate that opens the public /ppl route. It asks one question per
historical anchor — *what fraction of the simulated terminal prices came in
below the price that actually printed?* — and checks that the resulting series
is Uniform(0,1). See `app/services/ppl/calibrate.py` for the theory.

Session convention, and why it matters: the research tables are ET regular
session (`ohlcv_30m` 13 bars, `ohlcv_hourly` 7, `ohlcv_daily` 1). The live tier
resamples CH `ohlcv_1m`, which carries the full ET 04:00-20:00 day. PPL's
`rth_only` default restricts the live path to RTH so the two agree — a coverage
number measured on one geometry says nothing about the other.

  poetry run python scripts/ppl_calibrate.py --symbols SPY --interval 1d
  poetry run python scripts/ppl_calibrate.py --symbols SPY,QQQ,IWM --interval 30m
  poetry run python scripts/ppl_calibrate.py --symbols SPY --interval 1d \
      --horizon 40 --drift-mode state          # does drift help or hurt?
  poetry run python scripts/ppl_calibrate.py --symbols SPY --interval 1d --out data/ppl/
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

import numpy as np  # noqa: E402

from app.services.ppl.calibrate import (  # noqa: E402
    COVERAGE_TOLERANCE,
    CoverageBand,
    ks_uniform,
    run_pit,
    split_by_volatility_regime,
)
from app.services.ppl.schemas import PplParams  # noqa: E402
from app.services.ppl.service import PplService  # noqa: E402

# Research tables, by the interval they hold. 4h has no research table — it
# would have to be rolled up from ohlcv_30m first, so it is not calibratable
# yet and the CLI says so rather than silently substituting another interval.
_TABLES: dict[str, str] = {
    "30m": "ohlcv_30m",
    "1h": "ohlcv_hourly",
    "1d": "ohlcv_daily",
}


def load_frame(symbol: str, interval: str, start: str | None, end: str | None) -> pd.DataFrame:
    """Read one symbol's full history from its research table."""
    from app.db.client import get_client

    table = _TABLES[interval]
    where = ["symbol = {s:String}"]
    params: dict = {"s": symbol.upper()}
    if start:
        where.append("timestamp >= {start:DateTime64(3)}")
        params["start"] = pd.Timestamp(start).to_pydatetime()
    if end:
        where.append("timestamp <= {end:DateTime64(3)}")
        params["end"] = pd.Timestamp(end).to_pydatetime()

    rows = get_client().query(
        f"SELECT timestamp, open, high, low, close FROM {table} FINAL "
        f"WHERE {' AND '.join(where)} ORDER BY timestamp",
        parameters=params,
    ).result_rows
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame.set_index("timestamp").astype(float)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True, help="comma list, e.g. SPY,QQQ")
    ap.add_argument("--interval", default="1d", choices=sorted(_TABLES))
    ap.add_argument("--horizon", type=int, default=40, help="bars simulated forward")
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--stride", type=int, default=0, help="0 = horizon (near-independent)")
    ap.add_argument("--min-history", type=int, default=800)
    ap.add_argument("--max-anchors", type=int, default=0, help="0 = no cap")
    ap.add_argument("--drift-mode", default="zero", choices=["zero", "unconditional", "state"])
    ap.add_argument(
        "--vol-model", default="gjr_fhs", choices=["gjr_fhs", "ewma_t"],
        help="gjr_fhs = GJR-GARCH + filtered historical simulation (the model). "
             "ewma_t = constant-vol Student-t (the null it replaced). Run both "
             "to measure the difference rather than assert it.",
    )
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--out", default="", help="directory for per-symbol JSON")
    ap.add_argument(
        "--pooled", action="store_true",
        help="also report one aggregate over all symbols. Single-symbol n is "
             "too small for the +-5pp gate to discriminate (see spec 6.5).",
    )
    a = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    params = PplParams(
        interval=a.interval,
        horizon_bars=a.horizon,
        n_paths=a.paths,
        n_render_paths=min(500, a.paths),
        drift_mode=a.drift_mode,
        vol_model=a.vol_model,
    )
    # Bars come from the research table already RTH-restricted; re-filtering is
    # a no-op but would silently drop rows if a table convention ever changed.
    params = params.model_copy(update={"rth_only": False})

    # The harness never fetches: it is handed each prefix explicitly.
    service = PplService(bars_fn=lambda *_a, **_k: [])

    print(
        f"PPL calibration · {a.interval} ({_TABLES[a.interval]}) · H={a.horizon} · "
        f"vol={a.vol_model} · drift={a.drift_mode} · {a.paths} paths · "
        f"gate ±{COVERAGE_TOLERANCE * 100:.0f}pp\n"
    )

    results, failures = [], []
    for symbol in symbols:
        frame = load_frame(symbol, a.interval, a.start or None, a.end or None)
        if frame.empty:
            print(f"{symbol}: NO DATA in {_TABLES[a.interval]} — skipped\n")
            failures.append(symbol)
            continue

        try:
            res = run_pit(
                service, symbol, frame, params,
                stride=a.stride or None,
                min_history=a.min_history,
                max_anchors=a.max_anchors or None,
            )
        except ValueError as exc:
            print(f"{symbol}: {exc}\n")
            failures.append(symbol)
            continue

        print(res.summary())
        regimes = split_by_volatility_regime(res, frame)
        for name, bands in regimes.items():
            worst = max(bands, key=lambda b: abs(b.error))
            print(
                f"  [{name:>8}] n={bands[0].n:4d}  worst miss "
                f"q{worst.quantile:.2f} {worst.error * 100:+.1f}pp"
            )
        print()

        results.append((res, regimes))
        if not res.passed:
            failures.append(symbol)

        if a.out:
            out_dir = Path(a.out)
            out_dir.mkdir(parents=True, exist_ok=True)
            path = (
                out_dir
                / f"pit_{symbol}_{a.interval}_h{a.horizon}_{a.vol_model}_{a.drift_mode}.json"
            )
            path.write_text(json.dumps({
                "symbol": symbol,
                "interval": a.interval,
                "table": _TABLES[a.interval],
                "horizon_bars": a.horizon,
                "drift_mode": a.drift_mode,
                "vol_model": a.vol_model,
                "n_paths": a.paths,
                "stride": res.stride,
                "n_effective": res.n_effective,
                "mean_pit": res.mean_pit,
                "ks_stat": res.ks_stat,
                "ks_p": res.ks_p,
                "passed": res.passed,
                "coverage": {
                    f"{b.quantile:.2f}": {
                        "realized": b.realized, "error": b.error,
                        "se": b.standard_error, "z": b.z,
                        "within_tolerance": b.within_tolerance,
                    } for b in res.bands
                },
                "regimes": {
                    name: {f"{b.quantile:.2f}": {"realized": b.realized,
                                                 "error": b.error, "n": b.n}
                           for b in bands}
                    for name, bands in regimes.items()
                },
                "window_start": res.window_start.isoformat() if res.window_start else None,
                "window_end": res.window_end.isoformat() if res.window_end else None,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2))
            print(f"  wrote {path}\n")

    if a.pooled and results:
        _report_pooled([r for r, _ in results])

    # Report the aggregate honestly — a partial run is not a pass.
    print("─" * 66)
    passed = [r for r, _ in results if r.passed]
    print(f"{len(passed)}/{len(symbols)} symbol(s) PASS the ±{COVERAGE_TOLERANCE * 100:.0f}pp gate")
    if failures:
        print(f"not passing / not evaluated: {sorted(set(failures))}")
    return 0 if results and not failures else 1


def _report_pooled(results: list) -> None:
    """One coverage table over every symbol's PIT values.

    Pooling is how the +-5pp gate becomes able to discriminate: at n~108 the
    binomial SE at q=0.35 is 4.6pp, so the gate sits ~1 SE out and a perfectly
    calibrated model fails it often by chance.

    Caveat, stated because it matters: symbols move together, so pooled anchors
    are NOT independent and the effective sample is smaller than the row count.
    The pooled SE below is therefore optimistic — a lower bound on the true
    uncertainty, not an estimate of it. It still beats single-symbol n by a wide
    margin.
    """
    pit = np.concatenate([r.pit_values for r in results])
    n = int(pit.size)
    ks_stat, ks_p = ks_uniform(pit)
    bands = [
        CoverageBand(quantile=b.quantile,
                     realized=float((pit < b.quantile).mean()), n=n)
        for b in results[0].bands
    ]

    print("─" * 66)
    print(f"POOLED across {len(results)} symbol(s) · n={n} · "
          f"mean_PIT={pit.mean():.3f} · KS={ks_stat:.3f} (p={ks_p:.3f})")
    for b in bands:
        real = abs(b.error) > COVERAGE_TOLERANCE and abs(b.z) > 2
        verdict = "MISS" if real else ("wide" if abs(b.error) > COVERAGE_TOLERANCE else "ok  ")
        print(f"  q{b.quantile:.2f}: realized {b.realized * 100:5.1f}%  "
              f"err {b.error * 100:+5.1f}pp  SE {b.standard_error * 100:4.2f}pp  "
              f"z={b.z:+6.2f}  [{verdict}]")
    print("  a MISS needs BOTH |err| > 5pp (relevance) AND |z| > 2 (significance)")
    print("  SE assumes independent anchors — symbols co-move, so it is optimistic")


if __name__ == "__main__":
    raise SystemExit(main())
