# Elliott Wave engine

Pure Elliott Wave analysis: candidate generation, structural rules, Fibonacci
scoring, forward projections, nesting, and typed wave schemas.

This package must remain deterministic and free of database, provider, service,
and network imports. Persistence belongs to
[`../../services/elliott_store/`](../../services/elliott_store/); alert policy
belongs to [`../../services/alerts/`](../../services/alerts/).

Tests live in [`tests/`](tests/) and include structural purity and no-lookahead
gates. Shared synthetic wave fixtures used outside this module live in
[`../../../tests/support/`](../../../tests/support/).
