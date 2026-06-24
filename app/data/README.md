# Static application data

Owns curated, source-controlled data used to bootstrap the application.
Currently [`seed_universe.py`](seed_universe.py) defines the immutable equity
seed universe and its query helpers.

This package does not own runtime universe state. Active and streamed universes
belong to [`../services/universe/`](../services/universe/) and
[`../services/stream/`](../services/stream/). Adding or changing a symbol must
follow [`../../docs/standards/data/symbol_lifecycle.md`](../../docs/standards/data/symbol_lifecycle.md).

Tests live in [`tests/`](tests/):

```bash
poetry run pytest app/data/tests
```
