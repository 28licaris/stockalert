# Market-data providers

External-provider adapters for Alpaca, Polygon, and Schwab. [`base.py`](base.py)
defines the common streaming and historical-data interface; concrete modules
translate provider payloads into platform shapes.

Provider modules may know vendor APIs but must not own lake, ClickHouse, API, or
strategy policy. Select providers through [`../config.py`](../config.py), not
with direct environment reads. Corp-action and flat-file clients are Polygon
specialized inputs consumed by ingestion services.

## Schwab streamer supervision

`SchwabProvider` runs the streamer websocket on a supervised daemon thread. One
*session* is connect, LOGIN, SUBS (the full accumulated subscription set, replayed
on every reconnect), then receive. When a session ends for any reason except
`stop_stream()` the supervisor logs why (`Schwab streamer: session ended
reason=...`) and reconnects with exponential backoff (`SCHWAB_STREAMER_RECONNECT_BASE_S`
to `..._CAP_S`, jitter, reset after a session stays up `..._HEALTHY_AFTER_S`). A socket
that goes silent (no frame, heartbeats included, for `SCHWAB_STREAMER_IDLE_TIMEOUT_S`)
is declared dead. Every close logs its frame type and close code; non-zero ack codes
(12, 19, 30) log with their meaning. `stream_health()` and `request_reconnect()` are
the surface the stale-stream watchdog in
[`../services/stream/`](../services/stream/README.md) uses. Before this existed a
websocket close was a silent, permanent stop (see `docs/ISSUES.md`
`live-stream-silent-stall-reports-healthy`).

Unit tests use mocked transport in [`tests/`](tests/). Credentialed checks are
manual or integration tests:

```bash
poetry run pytest app/providers/tests
```
