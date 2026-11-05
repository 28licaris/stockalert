"""LakeArchive: find minute bars ClickHouse holds that the lake does not, and
(optionally) append them.

Why it exists (2026-11-05 audit): the live writer only archives the streamed
bars of a running process, so a day it missed, or a family it never covered
(futures), left bars whose ONLY copy was a laptop ClickHouse. Schwab's 1-minute
history reaches back ~48 days, so those bars cannot be re-fetched.

Contract:
  * Comparison is exact: set difference on (symbol, timestamp) per UTC day.
  * Dry run (``apply=False``, the default) never writes.
  * Applying is idempotent: a second run finds nothing missing.
  * Every append is verified from the lake side before it is counted. A write
    that "succeeded" but is not visible is reported as an error, never as done.
  * One bad day is isolated and reported; the other days still run.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Callable

from app.services.lake_integrity.contract import ChBarSource, LakeBarIndex, LakeBarWriter
from app.services.lake_integrity.schemas import Bar, BarFamily, DayGap, GapReport

logger = logging.getLogger(__name__)


class LakeArchive:
    BATCH_ROWS = 100_000

    def __init__(
        self,
        *,
        ch: ChBarSource,
        lake: LakeBarIndex,
        writer: LakeBarWriter,
        run_id_factory: Callable[[], str] = lambda: f"ch-archive-{uuid.uuid4()}",
    ) -> None:
        self._ch = ch
        self._lake = lake
        self._writer = writer
        self._run_id_factory = run_id_factory

    @classmethod
    def from_settings(cls) -> "LakeArchive":
        from app.services.lake_integrity.adapters import (
            ClickHouseBarSource,
            IcebergBarIndex,
            IcebergBarWriter,
        )

        return cls(ch=ClickHouseBarSource(), lake=IcebergBarIndex(), writer=IcebergBarWriter())

    # ──────────────────────────────────────────────────────────────────
    def run(
        self, family: BarFamily, *, since: date, until: date, apply: bool = False
    ) -> GapReport:
        if since > until:
            raise ValueError(f"since {since} is after until {until}")
        run_id = self._run_id_factory()
        days = self._ch.days(family, since, until)
        logger.info(
            "lake_archive[%s]: %d day(s) with unrecoverable-source bars in %s..%s (apply=%s run=%s)",
            family.name, len(days), since, until, apply, run_id,
        )
        results: list[DayGap] = []
        errors: list[str] = []
        for day in days:
            gap = self._one_day(family, day, apply=apply, run_id=run_id)
            results.append(gap)
            if gap.error:
                errors.append(f"{day}: {gap.error}")
            logger.info(
                "lake_archive[%s] %s: ch=%d missing=%d archived=%d%s",
                family.name, day, gap.ch_bars, gap.missing, gap.archived,
                f" ERROR {gap.error}" if gap.error else "",
            )
        missing_total = sum(g.missing for g in results)
        archived_total = sum(g.archived for g in results)
        if errors or (apply and archived_total < missing_total):
            status = "error"
        elif missing_total == 0:
            status = "complete"
        elif apply:
            status = "archived"
        else:
            status = "gaps_found"
        report = GapReport(
            family=family.name, since=since, until=until, apply=apply,
            days_checked=len(results),
            days_with_gaps=sum(1 for g in results if g.missing),
            missing_total=missing_total, archived_total=archived_total,
            status=status, days=results, errors=errors,
        )
        logger.info(
            "lake_archive[%s]: status=%s days=%d days_with_gaps=%d missing=%d archived=%d",
            family.name, report.status, report.days_checked, report.days_with_gaps,
            report.missing_total, report.archived_total,
        )
        return report

    # ──────────────────────────────────────────────────────────────────
    def _one_day(self, family: BarFamily, day: date, *, apply: bool, run_id: str) -> DayGap:
        try:
            bars = self._dedup(family, [self._normalize(family, b) for b in self._ch.bars(family, day)])
            # A normalized bar can land on the previous/next UTC day, so look up each
            # distinct day once (not once per bar: the key sets are large).
            have = {d: self._lake.keys(family, d) for d in {b.timestamp.date() for b in bars}}
            missing = [b for b in bars if b.key not in have[b.timestamp.date()]]
            gap = DayGap(day=day, ch_bars=len(bars), missing=len(missing))
            if not apply or not missing:
                return gap
            for i in range(0, len(missing), self.BATCH_ROWS):
                chunk = missing[i : i + self.BATCH_ROWS]
                written = self._writer.append(family, chunk, run_id)
                if written != len(chunk):
                    gap.error = f"writer reported {written} rows for a batch of {len(chunk)}"
                    return gap
            # Cross-side verification: what is now visible in the lake, not what the writer said.
            visible = {d: self._lake.keys(family, d, fresh=True) for d in {b.timestamp.date() for b in missing}}
            still = [b for b in missing if b.key not in visible[b.timestamp.date()]]
            gap.archived = len(missing) - len(still)
            if still:
                gap.error = f"{len(still)} bar(s) not visible in the lake after append"
            return gap
        except Exception as exc:  # noqa: BLE001 - boundary: one day must not abort the others
            logger.exception("lake_archive[%s] %s failed", family.name, day)
            return DayGap(day=day, ch_bars=0, missing=0, error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _normalize(family: BarFamily, bar: Bar) -> Bar:
        """Put the bar on its real minute label (see ``BarFamily.bar_minute_offset``)."""
        if not family.floor_to_minute and not family.bar_minute_offset:
            return bar
        t = bar.timestamp
        if family.floor_to_minute:
            t = t.replace(second=0, microsecond=0)
        t = t + timedelta(minutes=family.bar_minute_offset)
        return bar.model_copy(update={"timestamp": t})

    @staticmethod
    def _dedup(family: BarFamily, bars: list[Bar]) -> list[Bar]:
        """One bar per (symbol, timestamp); the earliest tag in ``family.sources`` wins."""
        rank = {s: i for i, s in enumerate(family.sources)}
        best: dict[tuple, Bar] = {}
        for b in bars:
            cur = best.get(b.key)
            if cur is None or rank.get(b.source, 99) < rank.get(cur.source, 99):
                best[b.key] = b
        return sorted(best.values(), key=lambda b: (b.timestamp, b.symbol))


def default_window(today: date, lookback_days: int) -> tuple[date, date]:
    """(since, until) covering the last ``lookback_days`` full UTC days through today."""
    return today - timedelta(days=lookback_days), today
