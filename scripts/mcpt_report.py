"""
Mechanical MCPT adjudication — no post-hoc judgment calls.

Two modes, composable:

  --battery 'data/mcpt/exp39_t1_*.json'
      Reads Tier-1 in-sample results (mcpt_insample.py JSON) and prints
      the family table with Benjamini-Hochberg q-values computed ACROSS
      the whole glob — the glob must be exactly one registered family
      battery (docs/research_hypotheses.md). Survivors = q <= alpha.

  --walkforward 'data/mcpt/exp37_t2_2006_2021*.jsonl' --metric sharpe_ratio
      Merges Tier-2 shard JSONLs (mcpt_walkforward.py): real row from
      any shard (must agree), permutations deduped by seed, single
      p-value on the chosen metric.

Usage:
  poetry run python scripts/mcpt_report.py --battery 'data/mcpt/exp39_t1_*.json'
  poetry run python scripts/mcpt_report.py --walkforward 'data/mcpt/exp37_t2_2006_2021_s*.jsonl'
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.sim.significance import benjamini_hochberg, mcpt_pvalue  # noqa: E402


def _battery(pattern: str, alpha: float) -> int:
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise SystemExit(f"no battery results match {pattern}")
    rows = []
    for p in paths:
        d = json.loads(Path(p).read_text())
        if d.get("kind") not in ("insample_mcpt", "insample_permutation"):
            raise SystemExit(f"{p} is not an in-sample MCPT result (kind={d.get('kind')})")
        rows.append({
            "family": d["family"], "window": f"{d['start']}..{d['end']}",
            "n_perms": d["result"]["n_permutations"], "real": d["result"]["real"],
            "null_mean": d["result"]["perm_mean"], "null_sd": d["result"]["perm_std"],
            "p": d["result"]["p_value"], "params": d["real_params"], "file": p,
        })
    qs = benjamini_hochberg([r["p"] for r in rows])
    for r, q in zip(rows, qs):
        r["q"] = q
    rows.sort(key=lambda r: r["q"])
    print(f"BATTERY REPORT ({len(rows)} families, BH across the glob, alpha={alpha})")
    print(f"{'family':<18}{'real PF':>9}{'null mean±sd':>16}{'p':>9}{'q':>9}  best params / verdict")
    for r in rows:
        verdict = "SURVIVOR -> Tier-2" if r["q"] <= alpha else "dead"
        print(f"{r['family']:<18}{r['real']:>9.4f}"
              f"{r['null_mean']:>9.4f}±{r['null_sd']:.4f}"
              f"{r['p']:>9.4f}{r['q']:>9.4f}  {r['params']}  [{verdict}]")
    survivors = [r for r in rows if r["q"] <= alpha]
    print(f"\n{len(survivors)}/{len(rows)} families survive at q<={alpha}")
    return 0


def _walkforward(pattern: str, metric: str, alpha: float) -> int:
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise SystemExit(f"no walk-forward shards match {pattern}")
    real_rows, perms_by_seed = [], {}
    for p in paths:
        for line in Path(p).read_text().splitlines():
            row = json.loads(line)
            if row["perm"] == -1:
                real_rows.append(row)
            else:
                perms_by_seed[row["seed"]] = row
    if not real_rows:
        raise SystemExit("no real (perm=-1) row found in shards")
    reals = {json.dumps(r["metrics"], sort_keys=True) for r in real_rows}
    if len(reals) > 1:
        raise SystemExit("shards disagree on the real run — mixed configs? refuse to merge")
    real = real_rows[0]["metrics"][metric]
    vals = [r["metrics"][metric] for r in perms_by_seed.values()]
    dropped = sum(1 for v in vals if v is None)
    if dropped:
        print(f"NOTE: {dropped} permutations had no {metric} (excluded)")
    res = mcpt_pvalue(real, [v for v in vals if v is not None],
                      greater_is_better=(metric != "max_drawdown"))
    print(f"WALK-FORWARD REPORT  {pattern}  metric={metric}  shards={len(paths)}")
    print(f"  {res.summary()}")
    print(f"  verdict at alpha={alpha}: "
          f"{'SIGNIFICANT' if res.p_value <= alpha else 'NOT significant'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--battery", help="glob of insample_mcpt JSON results")
    ap.add_argument("--walkforward", help="glob of walk-forward JSONL shards")
    ap.add_argument("--metric", default="sharpe_ratio")
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args(argv)
    if not a.battery and not a.walkforward:
        raise SystemExit("supply --battery and/or --walkforward")
    rc = 0
    if a.battery:
        rc |= _battery(a.battery, a.alpha)
    if a.walkforward:
        rc |= _walkforward(a.walkforward, a.metric, a.alpha)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
