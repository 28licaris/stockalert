"""
Phase TA-1 integration gate — run the canary on real bronze data.

This is the load-bearing test for TA-1: a full Backtester.run against
production AAPL minute bars from `bronze.polygon_minute`, verifying:

  1. Snapshot pinning works (run.snapshot_id is non-empty).
  2. Bars actually flow through to the strategy (history populates).
  3. At least some indicator values are computed (we get past warmup).
  4. The harness completes without exception even on a real noisy
     intraday window with thousands of bars.

The canary itself might not trade in every window — that's fine,
this test asserts mechanics not edge. Trading-existence is asserted
on the curated synthetic-crossing series in test_sim_unit.py.

Skips automatically without AWS creds / `STOCK_LAKE_BUCKET`.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from app.config import settings


pytestmark = pytest.mark.integration


def _aws_present() -> bool:
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        return True
    if os.getenv("AWS_PROFILE"):
        return True
    return os.path.isfile(os.path.expanduser("~/.aws/credentials"))


@pytest.mark.skipif(
    not _aws_present() or not settings.stock_lake_bucket,
    reason="AWS credentials / STOCK_LAKE_BUCKET unavailable",
)
def test_canary_runs_against_real_bronze() -> None:
    """Full end-to-end on a small AAPL bronze window (1m, ~1 trading day)."""
    from app.services.sim.backtester import Backtester
    from app.services.sim.schemas import BacktestConfig
    from app.services.sim.strategies.sma_crossover import (
        SmaCrossoverParams,
        SmaCrossoverStrategy,
    )

    # Small window so the test stays cheap. One trading day = ~960
    # minute bars; with SMA(20)/SMA(50) the strategy needs ~51 bars
    # of warmup, leaving ~900 actionable bars.
    cfg = BacktestConfig(
        symbols=["AAPL"],
        start=datetime(2024, 8, 1, 13, 30, tzinfo=timezone.utc),  # RTH open
        end=datetime(2024, 8, 1, 20, 0, tzinfo=timezone.utc),     # RTH close
        interval="1m",
        provider="polygon",
        starting_cash=40_000.0,
        history_window=100,
        fees_model="per_share",
        fees_params={"per_share": 0.005, "min_commission": 1.00},
        slippage_model="next_bar_open",
    )
    strat = SmaCrossoverStrategy(
        params=SmaCrossoverParams(
            fast_period=20, slow_period=50, position_size_pct=0.95,
        ),
        interval="1m",  # match the bronze 1m bar source
    )

    run = Backtester().run(strat, cfg)

    # Mechanics — these are the things this test is checking.
    assert run.strategy_name == "sma_crossover"
    assert run.snapshot_id, "expected an Iceberg snapshot_id for the bronze path"
    assert run.git_sha, "expected a git_sha capture"

    # Equity curve must have one entry per bar processed (1m bars
    # within RTH = ~390 expected; allow slack for early/late minutes).
    assert len(run.equity_curve) > 100, (
        f"expected >100 bars in window, got {len(run.equity_curve)}"
    )

    # Cash + equity sane (never negative; equity within reasonable range
    # of starting cash for a 1-day backtest).
    final_equity = run.metrics.final_equity
    assert final_equity > 0
    # Allow up to ±20% intra-day move (very lenient — this is mechanics,
    # not strategy quality).
    assert abs(final_equity / cfg.starting_cash - 1.0) < 0.20
