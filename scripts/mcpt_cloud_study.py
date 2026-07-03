"""
MCPT study driver: one command = a complete Tier-2 permutation study.

Runs the REAL backtest once, pre-seeds every shard with that row (so no
worker repeats the expensive real run), fans out N worker processes
(each an independent `mcpt_walkforward.py` shard with spaced seeds),
waits, merges the shards, and writes the adjudicated summary. Works
identically on this machine and on a CodeBuild/EC2 box — the only
difference is `--workers` and where `--out-prefix` points.

  # local, 6 workers x 16 perms = 96-perm null:
  poetry run python scripts/mcpt_cloud_study.py \
      --config configs/dyn_breakout_v2_top50_brake.yaml \
      --bars s3://<bucket>/research/mcpt/universe1000_2006_2026.parquet \
      --start 2006-01-01T00:00:00Z --end 2021-12-31T23:59:59Z \
      --workers 6 --perms-per-worker 16 \
      --out-prefix data/mcpt/studies/top50brake_0621

  # cloud (see scripts/codebuild/buildspec_mcpt_study.yml): same command,
  # --workers 32, --out-prefix s3://<bucket>/research/mcpt/studies/<name>

Shards under an s3:// prefix sync per-permutation and resume, so a
killed study re-launched with the same arguments continues where it
stopped.
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.mcpt_report import _walkforward  # noqa: E402
from scripts.research_bars import _is_s3, _s3_client, _split_s3  # noqa: E402

PY = sys.executable


def _wf_cmd(a, seed: int, n_perms: int, out: str) -> list[str]:
    cmd = [PY, "scripts/mcpt_walkforward.py", "--config", a.config,
           "--seed", str(seed), "--n-perms", str(n_perms),
           "--metric", a.metric, "--out", out]
    for flag, val in (("--start", a.start), ("--end", a.end),
                      ("--start-after", a.start_after), ("--bars", a.bars)):
        if val:
            cmd += [flag, val]
    return cmd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--bars", default=None,
                    help="bar snapshot (s3:// or local). REQUIRED off-machine; "
                         "omit only where ClickHouse is reachable")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--start-after", default=None)
    ap.add_argument("--metric", default="sharpe_ratio")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--perms-per-worker", type=int, default=16)
    ap.add_argument("--seed-base", type=int, default=1000)
    ap.add_argument("--out-prefix", required=True,
                    help="shard/report destination: local dir or s3://bucket/prefix")
    a = ap.parse_args(argv)

    s3_mode = _is_s3(a.out_prefix)
    prefix = a.out_prefix.rstrip("/")
    workdir = Path(tempfile.mkdtemp(prefix="mcpt_study_")) if s3_mode else Path(prefix)
    workdir.mkdir(parents=True, exist_ok=True)
    total = a.workers * a.perms_per_worker
    print(f"STUDY {prefix}: {a.workers} workers x {a.perms_per_worker} perms "
          f"= {total}-perm null (min resolvable p ~ {1 / (total + 1):.4f})", flush=True)

    # 1. Real run once (shard file with only the perm=-1 row).
    real_local = workdir / "real.jsonl"
    if not real_local.exists():
        print("running the REAL backtest once…", flush=True)
        subprocess.run(_wf_cmd(a, seed=0, n_perms=0, out=str(real_local)), check=True)
    real_row = real_local.read_text().splitlines()[0]
    print(f"real: {json.loads(real_row)['metrics']}", flush=True)

    # 2. Pre-seed every shard with the real row (skip shards that already exist —
    #    resume path), then launch workers.
    procs: list[tuple[int, subprocess.Popen]] = []
    for i in range(a.workers):
        name = f"shard_{i}.jsonl"
        if s3_mode:
            out = f"{prefix}/{name}"
            s3 = _s3_client()
            bucket, key = _split_s3(out)
            try:
                s3.head_object(Bucket=bucket, Key=key)
            except Exception:  # 404 -> fresh shard, seed it
                seed_file = workdir / f"seed_{name}"
                seed_file.write_text(real_row + "\n")
                s3.upload_file(str(seed_file), bucket, key)
        else:
            out = str(workdir / name)
            if not Path(out).exists():
                Path(out).write_text(real_row + "\n")
        log = (workdir / f"shard_{i}.log").open("a")
        p = subprocess.Popen(
            _wf_cmd(a, seed=a.seed_base + i * 1000, n_perms=a.perms_per_worker, out=out),
            stdout=log, stderr=subprocess.STDOUT)
        procs.append((i, p))
    print(f"launched {len(procs)} workers (logs: {workdir}/shard_*.log)", flush=True)

    # 3. Wait; fail loudly if any worker dies.
    failed = []
    while procs:
        for i, p in list(procs):
            rc = p.poll()
            if rc is None:
                continue
            procs.remove((i, p))
            status = "done" if rc == 0 else f"FAILED rc={rc}"
            print(f"  worker {i}: {status} ({len(procs)} still running)", flush=True)
            if rc != 0:
                failed.append(i)
        time.sleep(10)
    if failed:
        raise SystemExit(f"workers failed: {failed} — see {workdir}/shard_*.log; "
                         "re-run the same command to resume completed shards")

    # 4. Merge + adjudicate (download shards first in s3 mode).
    if s3_mode:
        s3 = _s3_client()
        bucket, _ = _split_s3(prefix)
        for i in range(a.workers):
            _, key = _split_s3(f"{prefix}/shard_{i}.jsonl")
            s3.download_file(bucket, key, str(workdir / f"shard_{i}.jsonl"))
    shard_glob = str(workdir / "shard_*.jsonl")
    print(f"\nmerging {len(glob.glob(shard_glob))} shards:", flush=True)
    rc = _walkforward(shard_glob, a.metric, alpha=0.05)

    summary = workdir / "study_summary.json"
    rows = []
    for f in sorted(glob.glob(shard_glob)):
        rows += [json.loads(line) for line in Path(f).read_text().splitlines()]
    summary.write_text(json.dumps({
        "kind": "mcpt_study", "config": a.config, "bars": a.bars,
        "start": a.start, "end": a.end, "start_after": a.start_after,
        "metric": a.metric, "workers": a.workers,
        "perms_per_worker": a.perms_per_worker, "seed_base": a.seed_base,
        "rows": rows}, indent=2))
    if s3_mode:
        bucket, key = _split_s3(f"{prefix}/study_summary.json")
        _s3_client().upload_file(str(summary), bucket, key)
        print(f"summary: {prefix}/study_summary.json", flush=True)
    else:
        print(f"summary: {summary}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
