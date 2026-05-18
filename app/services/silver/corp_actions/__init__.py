"""
Corp-actions ingest + build subpackage.

Two modules, two responsibilities — exact mirror of how OHLCV
ingest + build are organized:

- `polygon_ingest`: Polygon REST → `bronze.polygon_corp_actions`.
  Pulls splits + dividends; upserts the bronze table; idempotent.
- `build`: `bronze.{provider}_corp_actions` → `silver.corp_actions`.
  Merges all provider bronze tables with precedence; idempotent
  upsert into silver.

Adding a second provider later (e.g. SEC XBRL, IEX) means creating
a new `<provider>_ingest.py` module here + adding a precedence
entry in settings — `build.py` picks it up automatically.

Detailed contract: [docs/silver_layer_plan.md §4](../../../../docs/silver_layer_plan.md).
"""
from __future__ import annotations

from app.services.silver.corp_actions.build import SilverCorpActionsBuild
from app.services.silver.corp_actions.polygon_ingest import (
    PolygonCorpActionsBronzeIngest,
)

__all__ = [
    "PolygonCorpActionsBronzeIngest",
    "SilverCorpActionsBuild",
]
