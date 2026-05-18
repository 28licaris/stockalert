"""
Silver service — canonical, deduped, corp-action-adjusted OHLCV.

Layer 2 of the medallion architecture (bronze → silver → gold).

**Implementation contract:**
[docs/silver_layer_plan.md](../../../docs/silver_layer_plan.md).
Read it before changing anything here.

Public re-exports kept narrow so consumers depend on a stable
surface — internals (build job mechanics, schema field IDs) move
behind the package boundary.
"""
from __future__ import annotations

from app.services.silver.corp_actions import (
    PolygonCorpActionsBronzeIngest,
)
from app.services.silver.schemas import (
    CorpAction,
    CorpActionKind,
)

__all__ = [
    "CorpAction",
    "CorpActionKind",
    "PolygonCorpActionsBronzeIngest",
]
