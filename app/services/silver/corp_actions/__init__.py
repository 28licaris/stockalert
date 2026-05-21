"""
Corp-actions silver build subpackage (post-CV9).

The ingest module (`polygon_ingest.py`) was moved out to
`app/services/ingest/corp_actions.py` in CV9 because it no longer
writes to bronze — it targets `equities.market_corp_actions` directly.

What remains here is the silver build code, which is being retired
in Phase 1C alongside the rest of the silver layer. Until then,
`SilverCorpActionsBuild` keeps the silver readers fed.
"""
from __future__ import annotations

from app.services.silver.corp_actions.build import SilverCorpActionsBuild

__all__ = [
    "SilverCorpActionsBuild",
]
