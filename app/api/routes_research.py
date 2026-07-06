"""Research rankings — `/api/v1/research/*`.

Premium customer surface (gated by the subscription stub). Read-only guarded
rankings over the top-1000 liquid daily universe. See
docs/research_page_spec.md and app/services/research/rankings.py.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.api.routes_strategies import require_subscription
from app.api.schemas.research import RankingsResponse
from app.services.research.rankings import PRESETS, rank

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_subscription)])


@router.get("/research/rankings", response_model=RankingsResponse)
async def rankings(
    background: BackgroundTasks,
    preset: str = Query(..., description=f"One of: {', '.join(PRESETS)}"),
    lookback_days: int = Query(60, description="Momentum lookback: 20, 60, or 120."),
    top_n: int = Query(50, ge=1, le=200),
    min_dollar_vol: float = Query(10_000_000, ge=0, description="Liquidity floor (avg $/day)."),
    streak_min: int = Query(3, ge=1, le=30, description="Min streak length for streak presets."),
) -> RankingsResponse:
    if preset not in PRESETS:
        raise HTTPException(400, f"unknown preset {preset!r}; choose from {', '.join(PRESETS)}")

    res = await asyncio.to_thread(
        rank,
        preset,
        lookback_days=lookback_days,
        top_n=top_n,
        min_dollar_vol=min_dollar_vol,
        streak_min=streak_min,
    )

    # Fill any missing company names out-of-band so the next render is complete
    # (the read path is CH-only; warm is the sole provider-touching writer).
    missing = [r["symbol"] for r in res["rows"] if not r.get("name")]
    if missing:
        from app.services.instruments.names import warm

        background.add_task(warm, missing)

    return RankingsResponse(**res)
