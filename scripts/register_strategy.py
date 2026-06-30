"""
Register a strategy into the library (local save + S3 backup).

  poetry run python scripts/register_strategy.py --config configs/strategy_momentum.yaml

The strategy YAML carries PUBLIC metadata + a `config` dict (or `config_file`
pointing at a paper config). The full definition is saved locally and backed up to
s3://$STOCK_LAKE_BUCKET/strategies/<name>/v<version>_<utc>.json for safety.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from app.services.sim.library import service as lib  # noqa: E402
from app.services.sim.library.schemas import StrategyDefinition  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Strategy metadata YAML.")
    a = ap.parse_args(argv)
    raw = yaml.safe_load(Path(a.config).read_text())
    config = raw.get("config")
    if config is None and raw.get("config_file"):
        config = yaml.safe_load(Path(raw["config_file"]).read_text())
    if config is None:
        raise SystemExit("strategy config requires `config` or `config_file`")
    definition = StrategyDefinition(
        name=raw["name"], title=raw["title"], tagline=raw.get("tagline", ""),
        description=raw.get("description", ""), category=raw.get("category", "momentum"),
        version=int(raw.get("version", 1)), visibility=raw.get("visibility", "subscribers"),
        config=config,
    )
    result = lib.register(definition)
    print(f"\nregistered '{definition.name}' v{definition.version}")
    print(f"  local : {result.local_path}")
    print(f"  S3    : {result.s3_uri or '(skipped: ' + (result.s3_error or 'unknown') + ')'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
