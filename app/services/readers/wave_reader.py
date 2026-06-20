"""WaveReader — single read interface over Elliott Wave state.

Mirrors `IndicatorReader`: stateless, `from_settings()`, with a `backend` knob —
  * 'store'   : read the latest stored row from `<ns>.elliott_wave_labels`
  * 'compute' : recompute live for the current bar
  * 'auto'    : store first, fall back to compute when the store has no row

Same Pydantic response (`WaveStateResponse`) across backends and across the HTTP
route + MCP tools, so adding a surface is wiring, not a contract change.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field
from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThanOrEqual

from app.services.elliott_store.schema import asset_class_for, label_table_id
from app.services.iceberg_catalog import get_catalog
from app.signals.elliott.schemas import WaveLabeling

logger = logging.getLogger(__name__)

Backend = Literal["store", "compute", "auto"]


class WaveCountView(BaseModel):
    structure: str
    direction: str
    current_wave: str
    degree: Optional[int] = None
    probability: float = 0.0
    confidence: float = 0.0
    invalidation: Optional[float] = None
    targets: dict[str, float] = Field(default_factory=dict)
    rationale: str = ""
    nesting_score: float = 1.0
    forward: dict = Field(default_factory=dict)
    pivots: list[dict] = Field(default_factory=list)


class WaveStateResponse(BaseModel):
    symbol: str
    interval: str
    asset_class: str
    as_of_date: Optional[date] = None
    as_of_ts: Optional[datetime] = None
    as_of_price: Optional[float] = None
    primary: Optional[WaveCountView] = None
    secondary: Optional[WaveCountView] = None
    uncertainty: float = 1.0
    engine_ver: str = ""
    source: str = ""  # 'store' | 'compute'


def _from_labeling(lab: WaveLabeling, source: str) -> WaveStateResponse:
    def view(c, with_pivots: bool):
        if c is None:
            return None
        return WaveCountView(
            structure=c.structure, direction=c.direction, current_wave=c.current_wave,
            degree=c.degree, probability=c.probability, confidence=c.confidence,
            invalidation=c.invalidation_price, targets=c.fib_targets, rationale=c.rationale,
            nesting_score=c.nesting_score, forward=c.forward,
            pivots=[p.model_dump(mode="json") for p in c.pivots] if with_pivots else [],
        )
    return WaveStateResponse(
        symbol=lab.symbol, interval=lab.interval, asset_class=asset_class_for(lab.symbol),
        as_of_date=lab.as_of.date(), as_of_ts=lab.as_of, as_of_price=lab.as_of_price,
        primary=view(lab.primary, True), secondary=view(lab.secondary, False),
        uncertainty=lab.uncertainty, engine_ver=lab.engine_ver, source=source,
    )


def _clean(v):
    """pandas fills null cells with float('nan') (truthy!) — normalize to None."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _view_from_row(row: dict, prefix: str, with_pivots: bool) -> Optional[WaveCountView]:
    structure = _clean(row.get(f"{prefix}_structure"))
    if not structure:
        return None
    degree = _clean(row.get(f"{prefix}_degree"))
    return WaveCountView(
        structure=structure, direction=_clean(row.get(f"{prefix}_direction")) or "",
        current_wave=_clean(row.get(f"{prefix}_current_wave")) or "",
        degree=int(degree) if degree is not None else None,
        probability=_clean(row.get(f"{prefix}_probability")) or 0.0,
        confidence=_clean(row.get(f"{prefix}_confidence")) or 0.0,
        invalidation=_clean(row.get(f"{prefix}_invalidation")),
        targets=json.loads(_clean(row.get(f"{prefix}_targets")) or "{}"),
        rationale=_clean(row.get(f"{prefix}_rationale")) or "",
        nesting_score=_clean(row.get(f"{prefix}_nesting_score")) or 1.0,
        forward=json.loads(_clean(row.get(f"{prefix}_forward")) or "{}"),
        pivots=json.loads(_clean(row.get(f"{prefix}_pivots")) or "[]") if with_pivots else [],
    )


def _row_to_response(row: dict) -> WaveStateResponse:
    return WaveStateResponse(
        symbol=row["symbol"], interval=row["interval"],
        asset_class=row.get("asset_class") or asset_class_for(row["symbol"]),
        as_of_date=_clean(row.get("as_of_date")), as_of_ts=_clean(row.get("as_of_ts")),
        as_of_price=_clean(row.get("as_of_price")),
        primary=_view_from_row(row, "p", True), secondary=_view_from_row(row, "s", False),
        uncertainty=_clean(row.get("uncertainty")) if _clean(row.get("uncertainty")) is not None else 1.0,
        engine_ver=_clean(row.get("engine_ver")) or "", source="store",
    )


class WaveReader:
    """Read Elliott Wave state for a symbol/interval. Cheap to construct."""

    @classmethod
    def from_settings(cls) -> "WaveReader":
        return cls()

    def get_state(self, symbol: str, interval: str = "1d", *,
                  backend: Backend = "auto") -> WaveStateResponse:
        symbol = symbol.upper() if not symbol.startswith("/") else symbol
        if backend in ("store", "auto"):
            stored = self._read_latest(symbol, interval)
            if stored is not None:
                return stored
            if backend == "store":
                return WaveStateResponse(symbol=symbol, interval=interval,
                                         asset_class=asset_class_for(symbol), source="store")
        # compute (or auto-fallback)
        from app.services.elliott_store.recompute import compute_labeling
        lab = compute_labeling(symbol, interval)
        if lab is None:
            return WaveStateResponse(symbol=symbol, interval=interval,
                                     asset_class=asset_class_for(symbol), source="compute")
        return _from_labeling(lab, "compute")

    def get_history(self, symbol: str, interval: str = "1d", *,
                    start: Optional[date] = None,
                    end: Optional[date] = None) -> list[WaveStateResponse]:
        symbol = symbol.upper() if not symbol.startswith("/") else symbol
        df = self._scan(symbol, interval, start, end)
        if df is None or df.empty:
            return []
        df = df.sort_values(["as_of_date", "computed_at"])
        # one row per as_of_date — the latest computed_at for that day
        df = df.groupby("as_of_date", as_index=False).last()
        return [_row_to_response(r) for r in df.to_dict("records")]

    def list_latest(self, interval: str = "1d") -> list[WaveStateResponse]:
        """Latest stored count per symbol across both namespaces (universe scan)."""
        out: list[WaveStateResponse] = []
        for asset_class in ("equity", "future"):
            df = self._scan_all(asset_class, interval)
            if df is None or df.empty:
                continue
            df = df.sort_values(["symbol", "as_of_date", "computed_at"])
            latest = df.groupby("symbol", as_index=False).last()
            out.extend(_row_to_response(r) for r in latest.to_dict("records"))
        return out

    # -- store access -------------------------------------------------------
    def _scan_all(self, asset_class: str, interval: str):
        try:
            table = get_catalog().load_table(label_table_id(asset_class))
        except Exception as exc:
            logger.info("wave_reader: store unavailable for %s: %s", asset_class, exc)
            return None
        try:
            return table.scan(row_filter=EqualTo("interval", interval)).to_pandas()
        except Exception as exc:
            logger.warning("wave_reader: universe scan failed (%s): %s", asset_class, exc)
            return None

    def _scan(self, symbol: str, interval: str, start, end):
        try:
            table = get_catalog().load_table(label_table_id(asset_class_for(symbol)))
        except Exception as exc:  # table not created yet, etc.
            logger.info("wave_reader: store unavailable for %s: %s", symbol, exc)
            return None
        flt = And(EqualTo("symbol", symbol), EqualTo("interval", interval))
        if start is not None:
            flt = And(flt, GreaterThanOrEqual("as_of_date", start.isoformat()))
        if end is not None:
            flt = And(flt, LessThanOrEqual("as_of_date", end.isoformat()))
        try:
            return table.scan(row_filter=flt).to_pandas()
        except Exception as exc:
            logger.warning("wave_reader: scan failed for %s: %s", symbol, exc)
            return None

    def _read_latest(self, symbol: str, interval: str) -> Optional[WaveStateResponse]:
        df = self._scan(symbol, interval, None, None)
        if df is None or df.empty:
            return None
        row = df.sort_values(["as_of_date", "computed_at"]).iloc[-1].to_dict()
        return _row_to_response(row)
