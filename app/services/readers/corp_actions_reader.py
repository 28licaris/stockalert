"""
CorpActionsReader — read service for `silver.corp_actions`.

Reads the canonical, provider-precedence-resolved corp-actions table
(produced by `app/services/silver/corp_actions/build.py`). Per the
consumer contract ([silver_layer_plan §"The consumer contract"](../../../docs/silver_layer_plan.md)),
every consumer (chart, screener, indicator, backtest, MCP tool) reads
silver — **never bronze directly**.

This is the CH-independent path: agents and ML pipelines reading
corp-action history go through this reader and never touch ClickHouse.
Snapshot-pinnable for reproducibility.

Design contract:
  - Pure read; no writes; no global state beyond the catalog handle.
  - Pydantic shape (`CorpActionsResponse`) is what HTTP routes + MCP
    tools both surface — single contract, two surfaces.
  - Filters push down to Iceberg (year(ex_date) partition prune +
    symbol-sorted file skip).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, In, LessThanOrEqual
from pyiceberg.table import Table

from app.services.iceberg_catalog import get_catalog
from app.services.readers.schemas import CorpActionsResponse
from app.services.silver.schemas import CorpAction, CorpActionKind, silver_table_id

logger = logging.getLogger(__name__)


_TABLE_NAME = "corp_actions"


class CorpActionsReader:
    """Read silver.corp_actions via PyIceberg.

    Construct via `from_settings()` for production; pass `catalog` /
    `table` explicitly for tests.
    """

    def __init__(self, *, catalog=None, table: Optional[Table] = None) -> None:
        self._catalog = catalog
        self._table = table

    @classmethod
    def from_settings(cls) -> "CorpActionsReader":
        return cls()

    def _get_table(self) -> Table:
        if self._table is None:
            cat = self._catalog or get_catalog()
            self._table = cat.load_table(silver_table_id(_TABLE_NAME))
        return self._table

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def get_corp_actions(
        self,
        symbol: str,
        *,
        since: Optional[date] = None,
        until: Optional[date] = None,
        action_types: Optional[list[CorpActionKind]] = None,
        snapshot_id: Optional[str] = None,
    ) -> CorpActionsResponse:
        """Read corp-actions for `symbol` filtered by date window + kinds.

        Bounds (`since`, `until`) are inclusive on `ex_date`.
        `action_types` filters the kind (split / cash_dividend /
        stock_dividend / spinoff); None = all.

        Returns a `CorpActionsResponse` echoing the request shape so
        the result is self-documenting for downstream caching /
        replay.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return CorpActionsResponse(
                symbol=symbol or "",
                since=since,
                until=until,
                action_types=list(action_types) if action_types else None,
                snapshot_id=None,
                actions=[],
                count=0,
            )

        try:
            table = self._get_table()
        except Exception as e:
            # The table may not exist yet (initial system state before any
            # silver_corp_actions_build run). Return empty result rather
            # than raising — consumers shouldn't crash because corp-actions
            # haven't been built yet.
            logger.warning(
                "CorpActionsReader: silver.corp_actions not loadable (%s); "
                "returning empty result", e,
            )
            return CorpActionsResponse(
                symbol=sym,
                since=since,
                until=until,
                action_types=list(action_types) if action_types else None,
                snapshot_id=None,
                actions=[],
                count=0,
            )

        # Build the filter. Date bounds use Iceberg-native expressions
        # which push down to file-level skip via the partition (year(ex_date))
        # and column statistics.
        clauses = [EqualTo("symbol", sym)]
        if since is not None:
            clauses.append(GreaterThanOrEqual("ex_date", since))
        if until is not None:
            clauses.append(LessThanOrEqual("ex_date", until))
        if action_types:
            clauses.append(In("action_type", list(action_types)))
        row_filter = clauses[0] if len(clauses) == 1 else And(*clauses)

        try:
            scan = table.scan(row_filter=row_filter)
            arrow = scan.to_arrow()
        except Exception as e:
            logger.warning(
                "CorpActionsReader: scan failed for %s: %s; returning empty",
                sym, e,
            )
            return CorpActionsResponse(
                symbol=sym,
                since=since,
                until=until,
                action_types=list(action_types) if action_types else None,
                snapshot_id=None,
                actions=[],
                count=0,
            )

        actions = self._arrow_to_actions(arrow)

        # Capture the snapshot we just read against, for reproducibility.
        snap = table.current_snapshot()
        snap_id = str(snap.snapshot_id) if snap else None

        return CorpActionsResponse(
            symbol=sym,
            since=since,
            until=until,
            action_types=list(action_types) if action_types else None,
            snapshot_id=snap_id,
            actions=actions,
            count=len(actions),
        )

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _arrow_to_actions(arrow) -> list[CorpAction]:
        """Convert PyArrow Table → sorted list[CorpAction]."""
        if arrow.num_rows == 0:
            return []

        # PyArrow's to_pylist preserves the dict shape we need to feed
        # Pydantic. Sort by (ex_date, action_type) for deterministic
        # output — consumers can rely on temporal ordering.
        rows = arrow.to_pylist()
        rows.sort(key=lambda r: (r["ex_date"], r["action_type"]))

        out: list[CorpAction] = []
        for r in rows:
            ts = r.get("announced_at")
            if ts is not None and not isinstance(ts, datetime):
                # PyArrow may return a python datetime already; this
                # is just a defensive fallback.
                try:
                    ts = datetime.fromisoformat(str(ts))
                except (TypeError, ValueError):
                    ts = None
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            ing_ts = r.get("ingestion_ts")
            if ing_ts is not None and ing_ts.tzinfo is None:
                ing_ts = ing_ts.replace(tzinfo=timezone.utc)

            out.append(
                CorpAction(
                    symbol=r["symbol"],
                    ex_date=r["ex_date"],
                    action_type=r["action_type"],
                    factor=r.get("factor"),
                    cash_amount=r.get("cash_amount"),
                    announced_at=ts,
                    source_provider=r.get("source_provider") or "polygon",
                    ingestion_ts=ing_ts,
                    ingestion_run_id=r.get("ingestion_run_id"),
                )
            )
        return out
