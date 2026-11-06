"""DTOs for lake integrity. The only file other services import."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class BarFamily(BaseModel):
    """One family of minute bars: where ClickHouse keeps it and where the lake must.

    ``sources`` lists the ClickHouse ``source`` tags whose rows have NO other
    copy (they came from a live stream or a REST fetch, not from the lake), in
    priority order. Rows tagged ``lake-*`` came from the lake by definition, so
    they can never be missing from it and are not checked.
    """

    model_config = ConfigDict(frozen=True)

    name: Literal["equities", "futures"]
    ch_table: str
    lake_table: str
    sources: tuple[str, ...]
    has_adj_factor: bool
    # Some feeds stamp a streamed bar with its ARRIVAL time instead of its minute.
    # ``bar_minute_offset`` is the number of whole minutes to add after flooring a
    # stored timestamp to the minute, so it lands on the bar's real minute label.
    floor_to_minute: bool = False
    bar_minute_offset: int = 0


# Schwab is the only origin whose history cannot be re-fetched (1-minute REST
# reaches back ~48 days), so these are the rows whose only copy may be ClickHouse.
#
# Futures: Schwab's CHART_FUTURES stream stamps a bar with the time it ARRIVES,
# one minute after the bar it describes plus 1-3 s of delay (ClickHouse
# 2026-11-04 15:01:03.228 carries the prices of the lake's 15:00:00 bar).
# Measured 2026-11-05: 98% of stream bars price-match the lake bar at -1 minute
# on days the lake has coverage. Equities streams are minute-aligned.
EQUITIES = BarFamily(
    name="equities",
    ch_table="ohlcv_1m",
    lake_table="equities.schwab_universe",
    sources=("schwab-stream", "schwab-tipfill", "silver-schwab"),
    has_adj_factor=True,
)
FUTURES = BarFamily(
    name="futures",
    ch_table="futures_ohlcv_1m",
    lake_table="futures.schwab_futures",
    sources=("schwab-stream",),
    has_adj_factor=False,
    floor_to_minute=True,
    bar_minute_offset=-1,
)
FAMILIES: dict[str, BarFamily] = {EQUITIES.name: EQUITIES, FUTURES.name: FUTURES}


class Bar(BaseModel):
    """One minute bar as ClickHouse holds it. ``timestamp`` is timezone-aware UTC."""

    symbol: str
    timestamp: datetime
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    vwap: Optional[float] = None
    trade_count: Optional[int] = None
    source: str

    @property
    def key(self) -> tuple[str, datetime]:
        return (self.symbol, self.timestamp)


class DayGap(BaseModel):
    day: date
    ch_bars: int = Field(description="bars ClickHouse holds for the day with an unrecoverable source")
    missing: int = Field(description="of those, bars the lake did not have")
    archived: int = Field(default=0, description="missing bars appended and re-verified in the lake")
    error: Optional[str] = None


class GapReport(BaseModel):
    family: Literal["equities", "futures"]
    since: date
    until: date
    apply: bool
    days_checked: int
    days_with_gaps: int
    missing_total: int
    archived_total: int
    status: Literal["complete", "gaps_found", "archived", "error"]
    days: list[DayGap]
    errors: list[str] = Field(default_factory=list)

    @property
    def remaining(self) -> int:
        return self.missing_total - self.archived_total


# ── State snapshot ────────────────────────────────────────────────────────

class TableEntry(BaseModel):
    name: str
    rows: int
    bytes: int
    sha256: str
    ddl: str = Field(description="SHOW CREATE TABLE output; restore re-creates the table from it")
    key: str


class FileEntry(BaseModel):
    name: str
    bytes: int
    sha256: str
    key: str


class CoverageRow(BaseModel):
    """What a bar table held for one symbol when the snapshot was taken."""

    symbol: str
    first: datetime
    last: datetime
    rows: int


class SnapshotManifest(BaseModel):
    snapshot_id: str
    created_at: datetime
    database: str
    tables: list[TableEntry]
    files: list[FileEntry] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    coverage: dict[str, list[CoverageRow]] = Field(
        default_factory=dict,
        description="bar table -> per-symbol coverage; drives the lake rebuild windows and its verification",
    )
    failed: dict[str, str] = Field(default_factory=dict, description="table or file -> error")

    @property
    def complete(self) -> bool:
        return not self.failed


class TableRestore(BaseModel):
    name: str
    status: Literal["restored", "skipped_nonempty", "error"]
    rows: int = 0
    error: Optional[str] = None


class RestoreReport(BaseModel):
    snapshot_id: str
    tables: list[TableRestore]
    files_restored: int = 0
    files_skipped: int = 0
    errors: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and all(t.status != "error" for t in self.tables)
