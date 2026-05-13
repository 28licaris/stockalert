"""
Thin CRUD layer for journal tables (`trades`, `trade_notes`,
`account_snapshots`). Pure ClickHouse access — no Schwab API, no business
logic. Synchronous helpers are paired with `_async` wrappers that delegate
to `asyncio.to_thread` for use inside FastAPI request handlers.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.db.client import get_client
from app.services.journal_parser import TradeRecord

logger = logging.getLogger(__name__)


# ---------- Trades ----------


def insert_trades_batch(records: Iterable[TradeRecord]) -> int:
    """Insert `TradeRecord`s. Returns number of rows inserted."""
    rows: list[list] = []
    for r in records:
        rows.append([
            r.account_hash,
            int(r.activity_id),
            int(r.order_id),
            int(r.position_id),
            r.trade_time,
            r.symbol.upper(),
            r.asset_type,
            r.side,
            r.position_effect,
            float(r.quantity),
            float(r.price),
            float(r.gross_amount),
            float(r.fees),
            float(r.net_amount),
            r.status,
            r.raw_json or "",
            int(datetime.now(timezone.utc).timestamp() * 1000),  # version
        ])
    if not rows:
        return 0
    client = get_client()
    client.insert(
        "trades",
        rows,
        column_names=[
            "account_hash", "activity_id", "order_id", "position_id",
            "trade_time", "symbol", "asset_type", "side", "position_effect",
            "quantity", "price", "gross_amount", "fees", "net_amount",
            "status", "raw_json", "version",
        ],
    )
    return len(rows)


async def insert_trades_batch_async(records: Iterable[TradeRecord]) -> int:
    return await asyncio.to_thread(insert_trades_batch, list(records))


def list_trades(
    *,
    account_hash: Optional[str] = None,
    symbol: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 5000,
) -> list[dict]:
    """
    Return all matching trades joined with their notes (left-join, so trades
    without notes still appear). Ordered most-recent first for the journal UI.
    """
    where = ["1=1"]
    params: dict = {}
    if account_hash:
        where.append("account_hash = {acct:String}")
        params["acct"] = account_hash
    if symbol:
        where.append("symbol = {sym:String}")
        params["sym"] = symbol.upper()
    if start is not None:
        where.append("trade_time >= {start:DateTime64(3)}")
        params["start"] = start
    if end is not None:
        where.append("trade_time <= {end:DateTime64(3)}")
        params["end"] = end

    sql = f"""
        SELECT
            t.account_hash, t.activity_id, t.order_id, t.position_id,
            t.trade_time, t.symbol, t.asset_type, t.side, t.position_effect,
            t.quantity, t.price, t.gross_amount, t.fees, t.net_amount,
            t.status,
            n.strategy, n.tags, n.note, n.updated_at AS note_updated_at
        FROM trades t FINAL
        LEFT JOIN (
            SELECT account_hash, activity_id, strategy, tags, note, updated_at
            FROM trade_notes FINAL
        ) AS n
        ON  n.account_hash = t.account_hash
        AND n.activity_id  = t.activity_id
        WHERE {' AND '.join(where)}
        ORDER BY t.trade_time DESC, t.activity_id DESC
        LIMIT {{lim:UInt64}}
    """
    params["lim"] = int(limit)
    result = get_client().query(sql, parameters=params)
    cols = result.column_names
    return [dict(zip(cols, row)) for row in result.result_rows]


async def list_trades_async(**kwargs) -> list[dict]:
    return await asyncio.to_thread(list_trades, **kwargs)


def count_trades(account_hash: Optional[str] = None) -> int:
    sql = "SELECT count() FROM trades FINAL"
    params: dict = {}
    if account_hash:
        sql += " WHERE account_hash = {acct:String}"
        params["acct"] = account_hash
    return int(get_client().query(sql, parameters=params).result_rows[0][0])


# ---------- Notes ----------


def set_trade_note(
    *,
    account_hash: str,
    activity_id: int,
    strategy: str = "",
    tags: Optional[list[str]] = None,
    note: str = "",
) -> None:
    """Upsert a trade note. ReplacingMergeTree dedupes on (account_hash, activity_id)."""
    row = [
        account_hash,
        int(activity_id),
        strategy or "",
        list(tags or []),
        note or "",
        datetime.now(timezone.utc),
        int(datetime.now(timezone.utc).timestamp() * 1000),
    ]
    get_client().insert(
        "trade_notes",
        [row],
        column_names=[
            "account_hash", "activity_id", "strategy", "tags", "note",
            "updated_at", "version",
        ],
    )


async def set_trade_note_async(**kwargs) -> None:
    return await asyncio.to_thread(set_trade_note, **kwargs)


# ---------- Account snapshots ----------


def insert_account_snapshot(*, account_hash: str, snapshot_time: datetime,
                              payload: dict) -> None:
    """Snapshot a `/accounts` response into `account_snapshots`."""
    sa = (payload.get("securitiesAccount") or payload) if isinstance(payload, dict) else {}
    initial = sa.get("initialBalances") or {}
    current = sa.get("currentBalances") or initial or {}
    proj    = sa.get("projectedBalances") or current or {}

    row = [
        account_hash,
        snapshot_time,
        sa.get("type") or "",
        1 if sa.get("isDayTrader") else 0,
        int(sa.get("roundTrips") or 0),
        float(current.get("cashBalance") or 0.0),
        float(current.get("liquidationValue") or current.get("accountValue") or 0.0),
        float(current.get("longMarketValue") or 0.0),
        float(current.get("shortMarketValue") or 0.0),
        float(proj.get("buyingPower") or current.get("buyingPower") or 0.0),
        float(current.get("pendingDeposits") or 0.0),
        _json.dumps(payload, default=str)[:50_000],   # cap to keep rows bounded
        int(datetime.now(timezone.utc).timestamp() * 1000),
    ]
    get_client().insert(
        "account_snapshots",
        [row],
        column_names=[
            "account_hash", "snapshot_time", "account_type", "is_day_trader",
            "round_trips", "cash_balance", "liquidation_value",
            "long_market_value", "short_market_value", "buying_power",
            "pending_deposits", "raw_json", "version",
        ],
    )


async def insert_account_snapshot_async(**kwargs) -> None:
    return await asyncio.to_thread(insert_account_snapshot, **kwargs)


def latest_snapshot_per_account() -> list[dict]:
    """Return the most-recent snapshot per `account_hash` for the KPI strip."""
    # We cannot reuse the column name `snapshot_time` as our outer alias —
    # ClickHouse resolves the alias before the column, so the `argMax(...,
    # snapshot_time)` lines would suddenly see an aggregate as their second
    # argument (ILLEGAL_AGGREGATION). Aliasing to `snapshot_time_out` keeps
    # the inner references unambiguous.
    sql = """
        SELECT
            account_hash,
            max(snapshot_time)                        AS snapshot_time_out,
            argMax(account_type, snapshot_time)       AS account_type,
            argMax(is_day_trader, snapshot_time)      AS is_day_trader,
            argMax(round_trips, snapshot_time)        AS round_trips,
            argMax(cash_balance, snapshot_time)       AS cash_balance,
            argMax(liquidation_value, snapshot_time)  AS liquidation_value,
            argMax(long_market_value, snapshot_time)  AS long_market_value,
            argMax(short_market_value, snapshot_time) AS short_market_value,
            argMax(buying_power, snapshot_time)       AS buying_power,
            argMax(pending_deposits, snapshot_time)   AS pending_deposits
        FROM account_snapshots FINAL
        GROUP BY account_hash
        ORDER BY snapshot_time_out DESC
    """
    result = get_client().query(sql)
    cols = result.column_names
    rows = [dict(zip(cols, row)) for row in result.result_rows]
    # Restore canonical column name so callers don't need to know about
    # the alias workaround above.
    for r in rows:
        if "snapshot_time_out" in r:
            r["snapshot_time"] = r.pop("snapshot_time_out")
    return rows


async def latest_snapshot_per_account_async() -> list[dict]:
    return await asyncio.to_thread(latest_snapshot_per_account)


def list_snapshots(account_hash: str, *, limit: int = 1000) -> list[dict]:
    sql = """
        SELECT account_hash, snapshot_time, account_type, is_day_trader,
               round_trips, cash_balance, liquidation_value, long_market_value,
               short_market_value, buying_power, pending_deposits
        FROM account_snapshots FINAL
        WHERE account_hash = {acct:String}
        ORDER BY snapshot_time DESC
        LIMIT {lim:UInt64}
    """
    result = get_client().query(
        sql, parameters={"acct": account_hash, "lim": int(limit)},
    )
    cols = result.column_names
    return [dict(zip(cols, row)) for row in result.result_rows]


async def list_snapshots_async(account_hash: str, *, limit: int = 1000) -> list[dict]:
    return await asyncio.to_thread(list_snapshots, account_hash, limit=limit)
