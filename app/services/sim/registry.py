"""
Agent-run registry — write completed `RunResult`s to ClickHouse
`agent_runs`, read them back for replay / analysis.

The full RunResult (equity curve + trade log) can be large for
1-minute backtests; only a JSON-serialized slim form goes into the
CH row (`metrics_full`). For the future case where trades / curves
exceed an inline budget, archive to S3 and store a pointer — that
plumbing lands when we hit the row-size ceiling, not before.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from app.services.sim.schemas import RunResult

logger = logging.getLogger(__name__)


def _json_default(obj):
    """JSON serializer for datetime / Decimal / UUID / etc."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def write_run(run: RunResult) -> None:
    """
    Insert one row into `agent_runs`. Idempotency: the run_id is a
    fresh UUID per run, so re-running a backtest produces a NEW row
    (intentional — we want to see every execution attempt, not
    overwrite). For deduplication, query on (strategy_name,
    snapshot_id, config_json) — same triple = same logical run.

    Errors during write are logged but not raised — a failed registry
    write shouldn't drop the result the caller already received.
    Callers needing strict "wrote-or-bust" semantics should use
    `write_run_strict`.
    """
    try:
        write_run_strict(run)
    except Exception as exc:  # noqa: BLE001 — best-effort by default
        logger.warning("registry.write_run failed for %s: %s", run.run_id, exc)


def write_run_strict(run: RunResult) -> None:
    """Same as `write_run` but raises on failure."""
    from app.db.client import get_client

    metrics = run.metrics
    row = {
        "run_id": str(run.run_id),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "strategy_name": run.strategy_name,
        "strategy_version": run.strategy_version,
        "strategy_params": json.dumps(run.strategy_params, default=_json_default),
        "config": run.config.model_dump_json(),
        "snapshot_id": run.snapshot_id or "",
        "symbols": list(run.config.symbols),
        "interval": run.config.interval,
        "start_date": run.config.start.date(),
        "end_date": run.config.end.date(),
        "starting_cash": float(run.config.starting_cash),
        "total_return": float(metrics.total_return),
        "annualized_return": float(metrics.annualized_return or 0.0),
        "sharpe_ratio": float(metrics.sharpe_ratio or 0.0),
        "sortino_ratio": float(metrics.sortino_ratio or 0.0),
        "max_drawdown": float(metrics.max_drawdown),
        "win_rate": float(metrics.win_rate or 0.0),
        "profit_factor": float(metrics.profit_factor or 0.0),
        "n_trades": int(metrics.n_trades),
        "final_equity": float(metrics.final_equity),
        "metrics_full": metrics.model_dump_json(),
        "git_sha": run.git_sha or "",
    }
    client = get_client()
    client.insert(
        "agent_runs",
        [list(row.values())],
        column_names=list(row.keys()),
    )
    logger.info(
        "registry.write_run: inserted run_id=%s strategy=%s n_trades=%d total_return=%.4f",
        run.run_id, run.strategy_name, metrics.n_trades, metrics.total_return,
    )


def fetch_run(run_id: UUID | str) -> Optional[dict]:
    """
    Load one row by run_id. Returns a dict of columns or None.

    For reproducibility: re-instantiate strategy + config from the
    JSON columns, re-run the backtester, compare metrics. The
    `reproduce(run_id)` CLI (TA-1 follow-up) wraps this.
    """
    from app.db.client import get_client

    rid = str(run_id)
    result = get_client().query(
        "SELECT * FROM agent_runs WHERE run_id = {rid:UUID} LIMIT 1",
        parameters={"rid": rid},
    )
    if not result.result_rows:
        return None
    cols = result.column_names
    return dict(zip(cols, result.result_rows[0]))


def list_runs(
    strategy_name: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """
    List recent runs (newest first), optionally filtered by strategy.
    Used by analysis tools and (eventually) an MCP tool so an agent
    can ask "how have my strategies performed."
    """
    from app.db.client import get_client

    where = ""
    params: dict = {"lim": int(limit)}
    if strategy_name:
        where = "WHERE strategy_name = {name:String}"
        params["name"] = strategy_name

    result = get_client().query(
        f"""
        SELECT
            run_id, started_at, strategy_name, strategy_version,
            symbols, interval, start_date, end_date, n_trades,
            total_return, sharpe_ratio, max_drawdown, final_equity
        FROM agent_runs
        {where}
        ORDER BY started_at DESC
        LIMIT {{lim:UInt32}}
        """,
        parameters=params,
    )
    cols = result.column_names
    return [dict(zip(cols, row)) for row in result.result_rows]
