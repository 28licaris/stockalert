"""HTTP routes for Posterior Predictive Levels.

Mounted behind `require_operator` while the PIT calibration harness (P3) is
outstanding — spec D2: the public route does not open until coverage passes.

Spec: `docs/posterior_predictive_levels_spec.md`.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.ppl.contract import PplBuilder
from app.services.ppl.schemas import (
    SUPPORTED_INTERVALS,
    ConditioningParams,
    DriftMode,
    LevelMethod,
    PplParams,
    PplSnapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# No snapshot cache yet (spec §7.4). The simulation itself is ~1ms; the cost is
# the bar fetch, and caching that correctly means keying on the anchor bar,
# which isn't known until after the fetch. Deferred rather than half-done.


@lru_cache(maxsize=1)
def _build_service() -> PplBuilder:
    from app.services.ppl.service import PplService

    return PplService.from_settings()


def get_ppl_service() -> PplBuilder:
    """FastAPI dependency provider; tests override this."""
    return _build_service()


@router.get(
    "/ppl/{symbol}",
    response_model=PplSnapshot,
    summary="Posterior predictive fan and extracted levels for one symbol",
)
def get_posterior_predictive_levels(
    symbol: str,
    interval: str = Query("30m", description=f"One of: {', '.join(SUPPORTED_INTERVALS)}."),
    horizon_bars: int = Query(40, ge=1, le=400, description="Bars to simulate forward."),
    n_paths: int = Query(2000, ge=100, le=50_000, description="Paths to simulate."),
    n_render_paths: int = Query(300, ge=1, le=500, description="Paths returned for drawing."),
    method: LevelMethod = Query("quantile", description="Level extraction method."),
    drift_mode: DriftMode = Query(
        "zero",
        description=(
            "'zero' = no drift. 'unconditional' = the symbol's own baseline drift "
            "(no timing claim). 'state' = the shrunk per-state conditional drift, "
            "a timing claim this platform's evidence rejects."
        ),
    ),
    min_move_sigma_mult: float = Query(
        0.25, ge=0.0, description="min_move = this multiple of the horizon sigma."
    ),
    seed: Optional[int] = Query(None, description="Override the derived seed."),
    service: PplBuilder = Depends(get_ppl_service),
) -> PplSnapshot:
    """Build one fan.

    Deterministic: the same symbol at the same anchor bar returns an identical
    payload, so a reload never reshuffles the chart.

    The four levels are **quantiles of a simulated distribution**, not price
    targets — each carries its `quantile` so the UI can say which.
    """
    params = PplParams(
        interval=interval,
        horizon_bars=horizon_bars,
        n_paths=n_paths,
        n_render_paths=n_render_paths,
        method=method,
        drift_mode=drift_mode,
        min_move_sigma_mult=min_move_sigma_mult,
        seed=seed,
        conditioning=ConditioningParams(),
    )

    result = service.build(symbol, params)

    if result.ok and result.snapshot is not None:
        return result.snapshot

    # Predictable failures come back from the service as statuses, not
    # exceptions; translate each to the HTTP code that means the same thing.
    status_codes = {
        "no_data": 404,
        "insufficient_data": 422,
        "error": 400,
    }
    code = status_codes.get(result.status, 500)
    logger.info(
        "ppl: %s %s -> %s (%s)", symbol, interval, result.status, result.error
    )
    raise HTTPException(
        status_code=code,
        detail={
            "status": result.status,
            "error": result.error,
            **result.metadata,
        },
    )


@router.get("/ppl/{symbol}/meta", summary="Supported PPL parameters and defaults")
def get_ppl_meta(symbol: str) -> dict:
    """Everything the UI needs to build its controls without hardcoding."""
    defaults = PplParams()
    return {
        "symbol": symbol.upper().strip(),
        "supported_intervals": list(SUPPORTED_INTERVALS),
        "defaults": {
            "interval": defaults.interval,
            "horizon_bars": defaults.horizon_bars,
            "n_paths": defaults.n_paths,
            "n_render_paths": defaults.n_render_paths,
            "method": defaults.method,
            "drift_mode": defaults.drift_mode,
            "quantiles": list(defaults.quantiles),
            "min_move_sigma_mult": defaults.min_move_sigma_mult,
        },
        # Model-level, NOT per-symbol. The distinction is the whole point:
        # `scripts/ppl_calibrate.py` has been run and the model passes, but no
        # per-(symbol, interval) coverage record is stored yet, so a snapshot
        # still carries `calibration: null`. Reporting "passed" without that
        # qualifier would let a caller infer coverage for a symbol nobody
        # measured.
        "calibration_status": "model_validated_no_symbol_record",
        "calibration_note": (
            "The model passes its PIT gate pooled over SPY/QQQ/IWM/DIA/GLD/TLT "
            "at 1d (n=615, KS p=0.119) and 1h (n=4948, KS=0.028; not rejected "
            "for 5 of 6 symbols, against 1 of 6 for the model it replaced). "
            "No per-symbol coverage is stored, so individual snapshots report "
            "calibration=null. Re-run scripts/ppl_calibrate.py to measure a "
            "specific symbol."
        ),
        "calibration_command": (
            "poetry run python scripts/ppl_calibrate.py "
            f"--symbols {symbol.upper().strip()} --interval {defaults.interval} "
            "--drift-mode unconditional"
        ),
    }
