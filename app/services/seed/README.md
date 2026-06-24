# Seed service compatibility facade

Legacy API compatibility over [`../stream/`](../stream/). `SeedService`
preserves the older `list_seed`, `add`, `remove`, `import_bulk`, and
`bootstrap_if_empty` method names while delegating every call to the
`StreamService` singleton.

New code should import from `app.services.stream`; this package owns no table,
subscription, or persistence logic of its own. It remains in use by
[`../../api/routes_seed.py`](../../api/routes_seed.py), so it cannot be removed
until that API is migrated or retired.

## Files

| File | Purpose |
|---|---|
| `seed_service.py` | Backward-compatible adapter and singleton |
| `__init__.py` | Legacy public exports |

The backing service's unit tests live in [`../stream/tests/`](../stream/tests/).
API compatibility is covered by the seed endpoint integration tests in
[`../../../tests/integration/test_api_v1_seed.py`](../../../tests/integration/test_api_v1_seed.py).
