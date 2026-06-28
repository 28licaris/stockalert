"""Orchestration: assemble the sector-rotation dashboard.

`SectorRotationService.from_settings()` wires the default group registry +
RRG windows from config. `build_dashboard()` resolves the benchmark once,
scores every group against it, and returns a `RotationDashboard` result
object — groups that fail to resolve/score are surfaced in `excluded` with
a reason, never silently dropped.

Reads go through ClickHouse (the fast single-symbol hot tier), not the cold
lake — see `resolver`. The 12-symbol build is a handful of fast CH reads, so
it runs per request with no caching layer.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from app.config import settings
from app.services.sectors import definitions, rrg
from app.services.sectors.resolver import GroupResolutionError, resolve
from app.services.sectors.schemas import (
    ExcludedGroup,
    RotationDashboard,
    RotationGroup,
    SectorRotationState,
)

logger = logging.getLogger(__name__)


class SectorRotationService:
    def __init__(
        self,
        *,
        groups: list[RotationGroup],
        benchmark: str,
        ratio_window: int,
        mom_window: int,
        tail_weeks: int,
        lookback_days: int,
    ) -> None:
        self.groups = groups
        self.benchmark = benchmark
        self.ratio_window = ratio_window
        self.mom_window = mom_window
        self.tail_weeks = tail_weeks
        self.lookback_days = lookback_days

    @classmethod
    def from_settings(cls, benchmark: str | None = None) -> "SectorRotationService":
        bench = benchmark or definitions.benchmark_symbol()
        return cls(
            groups=definitions.default_groups(bench),
            benchmark=bench,
            ratio_window=settings.rrg_ratio_window,
            mom_window=settings.rrg_mom_window,
            tail_weeks=settings.rrg_tail_weeks,
            lookback_days=settings.rrg_lookback_days,
        )

    def _benchmark_close(self) -> pd.Series:
        """Daily close series for the benchmark, resolved once per build."""
        bench_group = RotationGroup(
            id=self.benchmark,
            name=self.benchmark,
            kind="etf",
            benchmark=self.benchmark,
            members=[self.benchmark],
        )
        return resolve(bench_group, lookback_days=self.lookback_days)

    def build_dashboard(self, tail_weeks: int | None = None) -> RotationDashboard:
        tail = tail_weeks if tail_weeks is not None else self.tail_weeks

        try:
            bench_close = self._benchmark_close()
        except GroupResolutionError as exc:
            # Without the benchmark nothing can be scored — fail loudly.
            raise GroupResolutionError(
                f"benchmark {self.benchmark!r} has no data: {exc}"
            ) from exc

        sectors: list[SectorRotationState] = []
        excluded: list[ExcludedGroup] = []
        as_of = bench_close.index[-1]
        as_of_date = pd.Timestamp(as_of).date() if not isinstance(as_of, date) else as_of

        for group in self.groups:
            try:
                group_close = resolve(group, lookback_days=self.lookback_days)
            except (GroupResolutionError, NotImplementedError) as exc:
                logger.warning("rotation: excluding %s — %s", group.id, exc)
                excluded.append(ExcludedGroup(group_id=group.id, reason=str(exc)))
                continue

            result = rrg.score(
                group_close,
                bench_close,
                ratio_window=self.ratio_window,
                mom_window=self.mom_window,
                tail_weeks=tail,
            )
            if not result.sufficient or result.current is None:
                reason = result.reason or "insufficient data"
                logger.warning("rotation: excluding %s — %s", group.id, reason)
                excluded.append(ExcludedGroup(group_id=group.id, reason=reason))
                continue

            sectors.append(
                SectorRotationState(
                    group_id=group.id,
                    name=group.name,
                    current=result.current,
                    tail=result.tail,
                    relative_strength=result.relative_strength,
                )
            )

        logger.info(
            "rotation: built dashboard benchmark=%s scored=%d excluded=%d as_of=%s",
            self.benchmark, len(sectors), len(excluded), as_of_date,
        )
        return RotationDashboard(
            benchmark=self.benchmark,
            as_of=as_of_date,
            tail_weeks=tail,
            sectors=sectors,
            excluded=excluded,
        )
