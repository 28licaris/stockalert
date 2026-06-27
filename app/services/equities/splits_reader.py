"""
Load split factors from ``equities.market_splits`` for read-time adjustment.

Splits live in their own small table (~50k rows) — separated from the ~3M-row
``market_corp_actions`` dividend store — so this is cheap: a full read is
sub-second and a per-symbol read is instant, with no dividend scan. See
docs/market_splits_spec.md.

Single source of the split-loading I/O, shared by ``AdjustedOhlcvReader`` and
``read_arrow``. Returns ``{}`` (→ identity adjustment) on any failure; never
raises — a missing/unreadable splits table must degrade to raw passthrough,
not break the read (NO_SILENT_FAILURES: the failure is logged).
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from app.services.equities.adjust import build_cum_factor_lookup
from app.services.equities.schemas import equities_table_id

logger = logging.getLogger(__name__)

_SPLITS_TABLE_NAME = "market_splits"


def load_cum_factor_lookup(
    *,
    table=None,
    catalog=None,
    symbols: Optional[Sequence[str]] = None,
):
    """Build the cumulative-future-split lookup from ``market_splits``.

    Args:
      table:   pre-loaded PyIceberg table (DI seam for tests). If None, the
               table is loaded from `catalog` (or the default catalog).
      catalog: catalog override; only used when `table` is None.
      symbols: restrict to these symbols (row-filter pushdown). None reads
               the whole tiny table — fine for whole-market adjustment.

    Returns ``{symbol: (ex_dates_asc, cum_factors)}`` (see
    ``app.services.equities.adjust.build_cum_factor_lookup``), or ``{}`` on
    any load/scan failure.
    """
    if table is None:
        try:
            if catalog is None:
                from app.services.iceberg_catalog import get_catalog
                catalog = get_catalog()
            table = catalog.load_table(equities_table_id(_SPLITS_TABLE_NAME))
        except Exception as e:  # noqa: BLE001 — boundary
            logger.warning(
                "splits_reader: market_splits not loadable (%s); "
                "adjusting with no splits (identity)", e,
            )
            return {}

    scan_kwargs: dict = {"selected_fields": ("symbol", "ex_date", "factor")}
    if symbols:
        from pyiceberg.expressions import In
        scan_kwargs["row_filter"] = In("symbol", list(symbols))
    try:
        arr = table.scan(**scan_kwargs).to_arrow()
    except Exception as e:  # noqa: BLE001 — boundary
        logger.warning(
            "splits_reader: market_splits scan failed (%s); "
            "adjusting with no splits", e,
        )
        return {}

    d = arr.to_pydict()
    return build_cum_factor_lookup(
        zip(d.get("symbol", []), d.get("ex_date", []), d.get("factor", []))
    )
