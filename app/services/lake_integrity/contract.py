"""Protocols the archive depends on. Production adapters live in adapters.py;
tests inject fakes, so no unit test touches ClickHouse, Glue or S3."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Protocol

import pyarrow as pa

from app.services.lake_integrity.schemas import Bar, BarFamily


class ChBarSource(Protocol):
    def days(self, family: BarFamily, since: date, until: date) -> list[date]:
        """UTC days in [since, until] on which ClickHouse holds surviving rows
        whose source is one of ``family.sources``."""

    def bars(self, family: BarFamily, day: date) -> list[Bar]:
        """The surviving rows for that UTC day whose source is in ``family.sources``."""


class LakeBarIndex(Protocol):
    def keys(self, family: BarFamily, day: date, *, fresh: bool = False) -> set[tuple[str, datetime]]:
        """Every (symbol, timestamp) the lake table holds for that UTC day.

        Implementations may cache. ``fresh=True`` must bypass any cache and read
        the lake as it is now: it is what proves an append is really visible."""


class LakeBarWriter(Protocol):
    def append(self, family: BarFamily, bars: list[Bar], run_id: str) -> int:
        """Append the bars to the lake table; return rows written. Raises on failure."""


class ChStateClient(Protocol):
    """The slice of ClickHouse the state snapshot needs."""

    def list_tables(self, database: str) -> list[tuple[str, str]]:
        """(name, engine) of every base table in ``database``."""

    def create_ddl(self, database: str, table: str) -> str: ...

    def read_table(self, database: str, table: str, *, final: bool) -> pa.Table: ...

    def row_count(self, database: str, table: str) -> int: ...

    def insert(self, database: str, table: str, data: pa.Table) -> None: ...

    def coverage(self, database: str, table: str) -> list[tuple[str, datetime, datetime, int]]:
        """(symbol, first timestamp, last timestamp, rows) per symbol of a bar table."""

    def command(self, sql: str) -> None: ...


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def size(self, key: str) -> Optional[int]:
        """Stored object size in bytes, or None if absent."""

    def list(self, prefix: str) -> list[str]: ...

    def delete(self, key: str) -> None: ...
