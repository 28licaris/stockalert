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

# IMPORTANT: do not import from app.services.silver.corp_actions at the
# package level. The corp_actions ingest module imports
# `app.providers.polygon_corp_actions`, which in turn imports CorpAction
# from `app.services.silver.schemas` — that import path triggers
# `silver/__init__.py` and creates a circular import.
#
# Consumers should use the deeper paths:
#   from app.services.silver.schemas import CorpAction, CorpActionKind
#   from app.services.silver.corp_actions import (
#       PolygonCorpActionsBronzeIngest, SilverCorpActionsBuild,
#   )
#
# Only the pure Pydantic types are safely re-exportable here — they
# don't import anything from the corp_actions subpackage.
from app.services.silver.schemas import (
    CorpAction,
    CorpActionKind,
)

__all__ = [
    "CorpAction",
    "CorpActionKind",
]
