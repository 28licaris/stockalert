# Wave alerts

Turns Elliott Wave state into actionable, typed alert plans. It owns alert
schemas, risk/reward construction, scan gates, and intraday scan orchestration.

| File | Purpose |
|---|---|
| `schemas.py` | `WaveAlert` contract |
| `service.py` | Pure alert construction and batch scanning |
| `intraday.py` | Intraday scanner orchestration |
| `__init__.py` | Supported package exports |

Wave counting belongs to [`../../signals/elliott/`](../../signals/elliott/);
reading stored wave state belongs to [`../readers/`](../readers/). Tests live in
[`tests/`](tests/).
