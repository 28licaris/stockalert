# `app/api/schemas/` — Pydantic models for the HTTP API

Every cockpit-facing endpoint declares its request + response shape
here. The FastAPI process emits these as `/openapi.json`, the
frontend's `npm run codegen` reads that, and the cockpit's
TypeScript types come out the other end. A backend rename breaks
the frontend build, not production.

See [docs/frontend_api_contracts.md](../../../docs/frontend_api_contracts.md)
for the full rules, audit, and phased rollout.

---

## Layout

```
schemas/
├── __init__.py        re-exports common primitives
├── common.py          ErrorResponse, Page[T], AssetType, HealthState, Interval, OkResponse
├── README.md          ← you are here
└── <topic>.py         (FE-CONTRACTS-2+ adds bars, signals, watchlists, sim, ...)
```

One file per topic. Topic files import from `common.py`; never the
other way around.

## Rules

1. **Every route returns a Pydantic model.** Routes with no useful
   payload return `OkResponse`. Routes returning bare numbers /
   strings wrap in a one-key model. Never `dict` / `list[dict]`.
2. **`AssetType` lives on every symbol-bearing schema.**
   Futures (`/MNQM26`) and equities (`AAPL`) share a string field
   but differ in normalization + market hours; the asset_type
   discriminator lets the cockpit / agent branch without parsing
   the symbol.
3. **Errors use `ErrorResponse`, never `{"detail": "..."}`.** The
   global handler in `app/main_api.py` converts `HTTPException` →
   `ErrorResponse`. Routes that raise `HTTPException(404, "thing not found")`
   become `{"code": "not_found", "message": "thing not found", ...}`
   automatically.
4. **Unbounded lists wrap in `Page[T]`.** Small fixed-size lists
   (e.g. watchlist members ≤500) stay as `list[T]`.
5. **Mutations are idempotent OR return 409.** Adding a symbol
   already in a watchlist returns 200 with `changed=[]`. Creating
   a duplicate-name watchlist returns 409 with `code="conflict"`.
6. **Datetimes are tz-aware ISO 8601.** Use `isoformat_z()` from
   common.py for naive timestamps so JavaScript parses them as
   UTC.

## Adding a new schema file

```python
# app/api/schemas/bars.py
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from app.api.schemas.common import AssetType, Interval, Page

class Bar(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float] = None
    interval: Interval
    source: Optional[str] = Field(
        default=None,
        description="Which CH source table served this bar: 'ohlcv_1m', 'ohlcv_5m', 'ohlcv_daily', or 'live'.",
    )

class BarsResponse(Page[Bar]):
    """Bars are paginated; the route accepts a cursor parameter."""
    symbol: str
    asset_type: AssetType
    interval: Interval
```

Then in the route:

```python
@router.get("/bars", response_model=BarsResponse)
async def list_bars(...): ...
```

## Migration from `dict`-returning routes

When migrating a legacy route:

1. Sketch the response Pydantic model in this folder.
2. Add `response_model=...` to the route decorator.
3. Return `{}.model_dump()` shaped data — FastAPI will validate.
4. Run `npm run codegen` in `frontend/`.
5. Migrate the call site in `frontend/src/api/queries.ts` to use
   `apiClient.GET(...)` and the generated types.
6. Delete the hand-rolled interface in `queries.ts`.

The order matters: backend first, then codegen, then frontend.
