#!/usr/bin/env python3
"""Minimal preview server for testing the EWT page on the elliot-wave branch.

Serves the built SPA + the read-only API routes the EWT page needs (wave, bars,
instruments) WITHOUT the full app lifespan — so it does NOT start the Schwab
live stream / monitors, and therefore won't collide with or disrupt a real
backend already running on :8000. Reads from the same lake + ClickHouse as the
real backend (via .env).

Run:  poetry run python scripts/ewt_preview_server.py        # serves on :8011
Open: http://localhost:8011/app/ewt/AAPL
"""
from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_health, routes_instruments, routes_signals, routes_wave

V1 = "/api/v1"
app = FastAPI(title="StockAlert EWT preview (no stream)")
for module in (routes_health, routes_signals, routes_instruments, routes_wave):
    app.include_router(module.router, prefix=V1)

_DIST = Path(__file__).resolve().parents[1] / "app" / "static" / "dist"
if (_DIST / "assets").is_dir():
    app.mount("/app/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")


@app.get("/app")
@app.get("/app/{path:path}")
def spa(path: str = "") -> FileResponse:
    """Serve a real asset if it exists, else index.html (client-side routing)."""
    candidate = _DIST / path
    if path and candidate.is_file():
        return FileResponse(str(candidate))
    return FileResponse(str(_DIST / "index.html"))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8011, log_level="warning")
