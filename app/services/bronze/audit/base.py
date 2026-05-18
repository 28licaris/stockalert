"""
Bronze audit framework — base types.

A `BronzeAuditCheck` runs against one bronze table at a time and
returns one or more `AuditResult` rows. The runner iterates every
registered check across every registered bronze table.

Every check MUST be safe to run in production (read-only, bounded
data scan, no mutations) and MUST tolerate empty/missing tables
(return appropriate AuditResult, never raise).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

# The bronze tables we audit. Adding a new bronze table = add an
# entry here. The runner iterates this list.
BRONZE_TABLES_TO_AUDIT: list[str] = [
    "polygon_minute",
    "schwab_minute",
]


# ─────────────────────────────────────────────────────────────────────
# Audit result shape
# ─────────────────────────────────────────────────────────────────────


class AuditStatus:
    """String constants for AuditResult.status (kept stringly-typed for
    JSON-safety; could swap for Literal['ok','warn','fail','skipped'])."""
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


class AuditSeverity:
    """How loudly to report a failing check.

    INFO  — informational; never trips exit code.
    WARN  — suspicious; exit code non-zero if --strict.
    FAIL  — broken; always trips non-zero exit code.
    """
    INFO = "info"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class AuditResult:
    """One audit finding for one (check, table) pair.

    A check that wants to emit several findings (e.g. one per source
    tag, one per partition) returns multiple AuditResults — the
    runner just appends them all.
    """
    check: str                           # check name (registry key)
    table: str                           # bronze table audited
    status: str                          # OK / WARN / FAIL / SKIPPED
    severity: str = AuditSeverity.INFO    # INFO / WARN / FAIL (cosmetic)
    message: str = ""                    # human-readable summary
    details: dict[str, Any] = field(default_factory=dict)   # structured
    error: Optional[str] = None          # exception text if check failed to run


# ─────────────────────────────────────────────────────────────────────
# Check Protocol
# ─────────────────────────────────────────────────────────────────────


@runtime_checkable
class BronzeAuditCheck(Protocol):
    """One bronze-layer check.

    Each registered class is instantiated once per audit run and called
    against every bronze table (via the runner). Implementations live
    in this package and register via `@register_check("<name>")`.
    """

    check_name: str

    def run(self, table_name: str) -> list[AuditResult]:
        """Audit one bronze table. NEVER raise — failures = AuditResult.

        Implementations MUST be read-only and bounded (no full-table
        scans without a `limit` or `filter`).
        """
        ...


# ─────────────────────────────────────────────────────────────────────
# Helpers shared across checks
# ─────────────────────────────────────────────────────────────────────


def safe_load_table(table_name: str):
    """Load a bronze table by short name, returning (table, error_msg).

    On error: returns (None, error_string) so checks can produce
    AuditResult(status=SKIPPED, error=...) without raising.
    """
    from app.services.bronze.schemas import bronze_table_id
    from app.services.iceberg_catalog import get_catalog
    from pyiceberg.exceptions import NoSuchTableError

    try:
        catalog = get_catalog()
        table = catalog.load_table(bronze_table_id(table_name))
        return table, None
    except NoSuchTableError:
        return None, f"table {table_name} does not exist"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
