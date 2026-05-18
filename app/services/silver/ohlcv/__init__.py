"""
Silver OHLCV build subpackage (TA-5.1).

Three modules:

- `normalize.py`: per-provider raw↔split-adjusted normalization.
  Polygon=raw → compute _adj. Schwab=split_adjusted → un-adjust to
  compute _raw. Reads `silver.corp_actions` for split factors.
- `merge.py`: provider-precedence merge of normalized bronze rows
  into one silver row per (symbol, ts).
- `build.py`: the orchestrator. Reads bronze, normalizes per
  provider, merges, computes bar_quality, upserts silver.

Detailed contract: [silver_layer_plan §3](../../../../docs/silver_layer_plan.md).
"""
from __future__ import annotations

__all__: list[str] = []
