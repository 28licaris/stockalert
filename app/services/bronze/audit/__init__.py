"""
Bronze-layer audit framework.

Bronze is the foundation: every backtest, chart, screener, MCP tool,
and agent decision reads data that traces back to bronze. **If bronze
is wrong or under-tested, everything downstream is wrong.** This
package is the test suite that catches bronze-level corruption,
schema drift, or quality regressions before they leak into silver.

**Pluggable, like the probe framework** — adding a new check means
writing a class that implements `BronzeAuditCheck` and registering
it via `@register_check(name)`. The runner picks it up automatically.

Run:

    poetry run python scripts/audit_bronze.py
    poetry run python scripts/audit_bronze.py --check schema
    poetry run python scripts/audit_bronze.py --out-json audit.json

See [README.md](README.md) for the framework contract and the rules
for adding new checks.
"""
from __future__ import annotations

from typing import Callable, Type, TypeVar

from app.services.bronze.audit.base import (
    AuditResult,
    AuditSeverity,
    AuditStatus,
    BronzeAuditCheck,
)

_CHECK_REGISTRY: dict[str, type] = {}

T = TypeVar("T")


def register_check(name: str) -> Callable[[Type[T]], Type[T]]:
    """Class decorator: register an audit check under `name`.

    Re-registering raises (fail-fast on duplicates).
    """
    def decorator(cls: Type[T]) -> Type[T]:
        if name in _CHECK_REGISTRY:
            raise ValueError(
                f"Audit check '{name}' already registered. Use a unique name."
            )
        _CHECK_REGISTRY[name] = cls
        return cls
    return decorator


def list_registered_checks() -> list[str]:
    return sorted(_CHECK_REGISTRY)


def build_all_checks() -> list[BronzeAuditCheck]:
    return [_CHECK_REGISTRY[name]() for name in sorted(_CHECK_REGISTRY)]


def build_check(name: str) -> BronzeAuditCheck:
    """Build a single check by name. Raises KeyError if unknown."""
    return _CHECK_REGISTRY[name]()


# Import every check module so their @register_check decorators fire.
# Adding a new check = add an import line here.
from app.services.bronze.audit import schema as _schema           # noqa: F401,E402
from app.services.bronze.audit import row_counts as _row_counts   # noqa: F401,E402
from app.services.bronze.audit import source_tags as _source_tags  # noqa: F401,E402
from app.services.bronze.audit import null_symbols as _null_symbols  # noqa: F401,E402
from app.services.bronze.audit import adjustment_status as _adj_status  # noqa: F401,E402


__all__ = [
    "AuditResult",
    "AuditSeverity",
    "AuditStatus",
    "BronzeAuditCheck",
    "build_all_checks",
    "build_check",
    "list_registered_checks",
    "register_check",
]
