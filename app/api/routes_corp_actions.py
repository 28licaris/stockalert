"""
Corp-actions HTTP route — dashboard + script-facing.

Thin adapter over `CorpActionsReader`. One endpoint:

  GET /api/corp-actions/{symbol}  — splits + dividends for a symbol,
                                    optionally windowed.

Reads from `silver.corp_actions` (the canonical consumer surface).
Bronze corp-action tables are NOT exposed here — per the consumer
contract, every reader hits silver.

Same `CorpActionsReader` instance backs the MCP `get_corp_actions`
tool. One service, two surfaces.
"""
from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.services.readers.corp_actions_reader import CorpActionsReader
from app.services.readers.schemas import CorpActionsResponse
from app.services.silver.schemas import CorpActionKind

logger = logging.getLogger(__name__)

router = APIRouter()


# Valid action_type values — narrow Literal for OpenAPI clarity.
_VALID_KINDS = frozenset({"split", "cash_dividend", "stock_dividend", "spinoff"})


@lru_cache(maxsize=1)
def _build_reader() -> CorpActionsReader:
    return CorpActionsReader.from_settings()


def get_corp_actions_reader() -> CorpActionsReader:
    """FastAPI dependency provider — override in tests."""
    return _build_reader()


@router.get(
    "/corp-actions/{symbol}",
    response_model=CorpActionsResponse,
)
def get_corp_actions(
    symbol: str = Path(..., min_length=1, description="Ticker symbol (case-insensitive)."),
    since: Optional[date] = Query(
        None,
        description="Lower bound on ex_date (inclusive). Omit for full history.",
    ),
    until: Optional[date] = Query(
        None,
        description="Upper bound on ex_date (inclusive). Omit for through-today.",
    ),
    action_types: Optional[str] = Query(
        None,
        description=(
            "Comma-separated action kinds to filter on "
            "(split, cash_dividend, stock_dividend, spinoff). "
            "Omit for all kinds."
        ),
    ),
    reader: CorpActionsReader = Depends(get_corp_actions_reader),
) -> CorpActionsResponse:
    """Return corp-action events for `symbol`.

    Reads from `silver.corp_actions` (provider-precedence-resolved
    canonical view). Returns empty `actions` if silver hasn't been
    built yet OR if no events match — never raises.
    """
    parsed_kinds: Optional[list[CorpActionKind]] = None
    if action_types:
        kinds = [k.strip() for k in action_types.split(",") if k.strip()]
        invalid = [k for k in kinds if k not in _VALID_KINDS]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown action_type(s): {invalid}. Valid: "
                    f"{sorted(_VALID_KINDS)}."
                ),
            )
        parsed_kinds = kinds  # type: ignore[assignment]

    if since is not None and until is not None and since > until:
        raise HTTPException(
            status_code=400,
            detail=f"since ({since}) must be <= until ({until}).",
        )

    return reader.get_corp_actions(
        symbol,
        since=since,
        until=until,
        action_types=parsed_kinds,
    )
