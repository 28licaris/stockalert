"""Elliott Wave engine — pure, deterministic, no-look-ahead wave labeling.

Public API:
    WaveEngine          — `.label(pivots, last_price, ...) -> WaveLabeling`
    WaveLabeling         — primary + secondary + alternates for one as-of bar
    WaveCandidate        — one self-consistent count
    Pivot                — re-exported from app.indicators.pivots

Purity: this package imports only `app.indicators.pivots` from the app tree —
never app.db / app.providers / app.services (tests/test_elliott_purity.py).
"""
from __future__ import annotations

from app.signals.elliott.schemas import Pivot, WaveCandidate, WaveLabeling
from app.signals.elliott.engine import WaveEngine

__all__ = ["WaveEngine", "WaveLabeling", "WaveCandidate", "Pivot"]
