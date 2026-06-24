# Active universe

Resolves the symbol set used by per-symbol jobs. The current active universe is
the curated seed floor plus symbols from active watchlists; callers may also
request the seed-only specification.

[`active_universe.py`](active_universe.py) owns the resolution rules and
`__init__.py` exposes the supported public functions and constants. Durable
stream membership belongs to [`../stream/`](../stream/), and static seed data
belongs to [`../../data/`](../../data/).

Unit tests live in [`tests/`](tests/):

```bash
poetry run pytest app/services/universe/tests
```
