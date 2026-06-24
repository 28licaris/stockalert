"""
SourceSpec registry — the modular half of the lake read layer.

Spec: docs/lake_read_layer_design.md §3.1.

The cold read path unions one-or-more lake tables (per security type +
data provider) and dedups on (symbol, timestamp) by precedence. This
registry is the *single place* that declares what those sources are.
Onboarding a new provider (Alpaca, …) is one `SourceSpec` entry — no
edit to the union/dedup logic in `read_arrow.py`.

This encodes the v2 insight that **adjustment is a provider-specific
concern, not a pipeline layer**: each source owns its adjustment
behaviour (`computed` vs `pass_through`) and its dedup precedence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence


@dataclass(frozen=True)
class SourceSpec:
    """One pluggable cold-path source.

    Attributes:
      name:        logical key, e.g. "polygon_adjusted". Used to select
                   a subset of sources and for provenance.
      table_id:    fully-qualified PyIceberg identifier "<glue_db>.<table>".
      precedence:  higher wins on (symbol, timestamp) dedup. Today
                   polygon (2) > schwab (1) — the Spark adjustment job is
                   the source of truth for adj_factor across history.
      adjustment:  "computed" (provider raw + corp-actions → adjusted,
                   e.g. polygon) or "pass_through" (provider already
                   adjusted, adj_factor=1.0, e.g. schwab). Documents
                   semantics; the reader treats both as adjusted OHLCV.
      sorted_by_ts: the Iceberg table carries a sort order on
                   (symbol, timestamp). Lets the reader tell Polars the
                   per-symbol timestamp column is pre-sorted (lever 2:
                   merge instead of re-sort on the single-symbol path).
      provider_tag: fallback provenance tag stamped onto SilverBar.
                   source_provider when the row's own `source` column is
                   absent.
    """

    name: str
    table_id: str
    precedence: int
    adjustment: Literal["computed", "pass_through"]
    sorted_by_ts: bool
    provider_tag: str


def equity_sources() -> list[SourceSpec]:
    """The equities cold-path sources, polygon winning the union.

    `table_id` is resolved from settings lazily (the equities Glue DB is
    env-configurable), so importing this module never touches settings.
    """
    from app.services.equities.schemas import equities_table_id

    return [
        SourceSpec(
            name="polygon_adjusted",
            table_id=equities_table_id("polygon_adjusted"),
            precedence=2,
            adjustment="computed",
            sorted_by_ts=True,
            provider_tag="polygon-adjusted",
        ),
        SourceSpec(
            name="schwab_universe",
            table_id=equities_table_id("schwab_universe"),
            precedence=1,
            adjustment="pass_through",
            sorted_by_ts=False,
            provider_tag="schwab",
        ),
    ]


def resolve_sources(
    selection: Optional[Sequence[str]] = None,
    *,
    available: Optional[Sequence[SourceSpec]] = None,
) -> list[SourceSpec]:
    """Pick the sources to read.

    `selection` is None / empty → the full union (all `available`).
    Otherwise a list/tuple of source names selecting a subset, preserving
    the registry's precedence order. This is how a caller reads "any
    selected table or the default union" (requirement: pull AAPL from
    polygon deep history alone, or union it with the schwab universe).

    Unknown names raise ValueError — a typo'd source is a programming
    bug, not a data condition (readers/README.md hard rule).
    """
    avail = list(available) if available is not None else equity_sources()
    if not selection:
        return avail

    by_name = {s.name: s for s in avail}
    wanted = [str(n).strip() for n in selection if str(n).strip()]
    unknown = [n for n in wanted if n not in by_name]
    if unknown:
        raise ValueError(
            f"unknown source(s) {unknown}; known: {sorted(by_name)}"
        )
    # De-dup while preserving registry precedence order.
    chosen = [s for s in avail if s.name in set(wanted)]
    return chosen
