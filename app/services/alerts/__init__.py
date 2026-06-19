"""Elliott Wave alerts (EW-6) — turn wave counts into complete trade plans.

A WaveAlert is entry + stop (= count invalidation) + Fib target(s) + risk:reward
+ a day/swing tag, derived from the primary count. Surfaced via
`GET /api/v1/wave/alerts`, the `list_wave_alerts` MCP tool, and the EWT page's
scan tab.
"""
from __future__ import annotations

from app.services.alerts.schemas import WaveAlert
from app.services.alerts.service import build_alert, scan_alerts

__all__ = ["WaveAlert", "build_alert", "scan_alerts"]
