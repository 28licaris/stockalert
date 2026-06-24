# Active universe

Resolves the symbol set used by per-symbol jobs. The active universe is exactly
the active rows in ClickHouse `stream_universe`.

[`active_universe.py`](active_universe.py) owns the resolution rules and
`__init__.py` exposes the supported public functions and constants. Durable
membership belongs to [`../stream/`](../stream/). There is no static fallback;
ClickHouse read failures propagate rather than masquerading as an empty list.

Unit tests live in [`tests/`](tests/):

```bash
poetry run pytest app/services/universe/tests
```
