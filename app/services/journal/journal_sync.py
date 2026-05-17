"""
Periodic + on-demand sync of Schwab trader-API data into ClickHouse.

Three sync surfaces, all idempotent:
  - `sync_account_numbers`  : refresh the account_number <-> account_hash map
                              (in-memory, lasts the lifetime of the process)
  - `sync_balances`         : snapshot current balances + positions for every
                              known account into `account_snapshots`
  - `sync_trades`           : pull last N days of TRADE transactions for every
                              account, parse, insert into `trades`
                              (ReplacingMergeTree on activity_id dedupes)

A background loop runs `sync_balances` + `sync_trades` every 5 minutes by
default. The throttle is applied here (not in BackfillService) because these
have different semantics: account state changes continuously through the
trading day, so 5-minute polling is reasonable. Manual triggers (`force=True`)
always bypass the throttle.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from app.db import journal_repo
from app.providers.base import DataProvider
from app.services.journal.journal_parser import TradeRecord, parse_transaction

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    accounts: int = 0
    snapshots: int = 0
    trades_fetched: int = 0
    trades_inserted: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def to_dict(self) -> dict:
        return {
            "accounts": self.accounts,
            "snapshots": self.snapshots,
            "trades_fetched": self.trades_fetched,
            "trades_inserted": self.trades_inserted,
            "errors": self.errors or [],
        }


class JournalSyncService:
    """
    Singleton orchestrator for journal sync. Owns:
      - the `account_number -> account_hash` map (refreshed on demand)
      - the background sync loop
      - a small throttle so /api/journal/sync can't hammer Schwab.
    """

    # Minimum interval between automatic syncs (per-method). Manual calls
    # with `force=True` bypass this.
    THROTTLE_BALANCES = timedelta(minutes=5)
    THROTTLE_TRADES = timedelta(minutes=5)
    # How far back to look for new trades each sync. We over-fetch slightly so
    # late-reporting fills can still be picked up; ReplacingMergeTree handles
    # the dedupe.
    TRADE_LOOKBACK_DAYS = 30

    def __init__(self, *, provider_factory: Optional[Callable[[], DataProvider]] = None,
                  now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._provider_factory = provider_factory
        self._provider: Optional[DataProvider] = None
        self._now_fn = now_fn
        # account_number (str) -> account_hash (str). Filled by sync_account_numbers().
        self._number_to_hash: dict[str, str] = {}
        self._hash_to_number: dict[str, str] = {}
        self._last_balances_sync: Optional[datetime] = None
        self._last_trades_sync: Optional[datetime] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._started = False
        # Default loop cadence; tested independently.
        self._loop_interval_s: float = 5 * 60

    # ---------- lifecycle ----------

    async def start(self) -> None:
        self._started = True
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._loop(), name="journal_sync_loop")
        logger.info("JournalSyncService started (loop_interval=%ds)", self._loop_interval_s)

    async def stop(self) -> None:
        self._started = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._loop_task = None

    async def _loop(self) -> None:
        # Initial delay so app startup isn't bottlenecked on Schwab calls.
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            return
        while self._started:
            try:
                await self.sync_all()
            except Exception as e:
                logger.error("journal sync loop iteration failed: %s", e, exc_info=True)
            try:
                await asyncio.sleep(self._loop_interval_s)
            except asyncio.CancelledError:
                return

    # ---------- provider plumbing ----------

    def _get_provider(self) -> Optional[DataProvider]:
        if self._provider is not None:
            return self._provider
        if self._provider_factory is None:
            try:
                from app.config import get_provider
                self._provider_factory = get_provider  # type: ignore[assignment]
            except Exception as e:
                logger.warning("JournalSyncService: no provider factory available: %s", e)
                return None
        try:
            self._provider = self._provider_factory()
        except Exception as e:
            logger.warning("JournalSyncService: provider init failed: %s", e)
            self._provider = None
        return self._provider

    # ---------- public API ----------

    async def sync_account_numbers(self) -> int:
        """Refresh the account_number <-> account_hash map. Returns number of accounts."""
        provider = self._get_provider()
        if provider is None:
            return 0
        try:
            accts = await provider.get_account_numbers()  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("sync_account_numbers: provider error: %s", e)
            return 0
        if not isinstance(accts, list):
            return 0
        self._number_to_hash.clear()
        self._hash_to_number.clear()
        for a in accts:
            if not isinstance(a, dict):
                continue
            num = a.get("accountNumber")
            h   = a.get("hashValue")
            if num and h:
                self._number_to_hash[str(num)] = str(h)
                self._hash_to_number[str(h)] = str(num)
        return len(self._number_to_hash)

    def known_account_hashes(self) -> list[str]:
        return sorted(self._hash_to_number.keys())

    def hash_for_number(self, number: str) -> Optional[str]:
        return self._number_to_hash.get(str(number))

    def number_for_hash(self, h: str) -> Optional[str]:
        return self._hash_to_number.get(h)

    async def sync_balances(self, *, force: bool = False) -> int:
        """Snapshot every account's balances. Returns number of snapshots written."""
        if not force and self._last_balances_sync is not None:
            if self._now_fn() - self._last_balances_sync < self.THROTTLE_BALANCES:
                return 0
        provider = self._get_provider()
        if provider is None:
            return 0
        if not self._number_to_hash:
            await self.sync_account_numbers()
        try:
            payload = await provider.get_accounts()  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("sync_balances: provider error: %s", e)
            return 0

        accounts_list = payload if isinstance(payload, list) else [payload]
        written = 0
        snap_time = self._now_fn()
        for acct in accounts_list:
            if not isinstance(acct, dict):
                continue
            sa = acct.get("securitiesAccount") or acct
            number = str(sa.get("accountNumber") or "")
            h = self.hash_for_number(number)
            if not h:
                logger.debug("sync_balances: no hash for account %s, skipping", number)
                continue
            try:
                await journal_repo.insert_account_snapshot_async(
                    account_hash=h, snapshot_time=snap_time, payload=acct,
                )
                written += 1
            except Exception as e:
                logger.warning("sync_balances: insert failed for %s: %s", h, e)
        self._last_balances_sync = self._now_fn()
        return written

    async def sync_trades(self, *, days: int = TRADE_LOOKBACK_DAYS,
                            force: bool = False) -> SyncResult:
        """
        Pull TRADE transactions for every known account over `[now - days, now]`
        and persist them. Returns counts for observability.
        """
        result = SyncResult(errors=[])
        if not force and self._last_trades_sync is not None:
            if self._now_fn() - self._last_trades_sync < self.THROTTLE_TRADES:
                return result
        provider = self._get_provider()
        if provider is None:
            return result
        if not self._number_to_hash:
            await self.sync_account_numbers()
        result.accounts = len(self._number_to_hash)

        end = self._now_fn()
        start = end - timedelta(days=max(1, int(days)))
        for number, h in list(self._number_to_hash.items()):
            try:
                txs = await provider.get_transactions(  # type: ignore[attr-defined]
                    h, start=start, end=end, types="TRADE",
                )
            except Exception as e:
                msg = f"get_transactions({number}): {e}"
                logger.warning("sync_trades: %s", msg)
                result.errors.append(msg)
                continue
            if not isinstance(txs, list):
                continue
            records: list[TradeRecord] = []
            for tx in txs:
                try:
                    raw_json = _json.dumps(tx, default=str)[:50_000]
                except Exception:
                    raw_json = ""
                rec = parse_transaction(tx, account_hash=h, raw_json=raw_json)
                if rec is not None:
                    records.append(rec)
            result.trades_fetched += len(records)
            try:
                inserted = await journal_repo.insert_trades_batch_async(records)
                result.trades_inserted += int(inserted or 0)
            except Exception as e:
                msg = f"insert_trades({number}): {e}"
                logger.warning("sync_trades: %s", msg)
                result.errors.append(msg)
        self._last_trades_sync = self._now_fn()
        logger.info(
            "JournalSync: trades_fetched=%d trades_inserted=%d errors=%d",
            result.trades_fetched, result.trades_inserted, len(result.errors),
        )
        return result

    async def sync_all(self, *, force: bool = False) -> dict:
        """Run all syncs back-to-back. Used by both the loop and the manual route."""
        nacc = await self.sync_account_numbers()
        bal = await self.sync_balances(force=force)
        tr = await self.sync_trades(force=force)
        out = tr.to_dict()
        out["accounts"] = nacc
        out["snapshots"] = bal
        return out


# Module-level singleton; constructed at import time. Provider is resolved lazily.
journal_sync_service = JournalSyncService()
