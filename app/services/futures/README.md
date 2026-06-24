# Futures lake

Owns the separate `futures.*` Iceberg domain: schemas, tables, continuous-root
symbols, universe metadata, gaps, sinks, roll logic, and lake-to-ClickHouse
fills. Futures have no equity corporate-action adjustment tier.

Symbols use `/`-prefixed continuous roots such as `/ES`; preserve that routing
contract through storage and reader boundaries. The architecture is documented
in [`../../../docs/futures_data_plan.md`](../../../docs/futures_data_plan.md) and
the canonical lake conventions in
[`../../../docs/architecture_v2/`](../../../docs/architecture_v2/).

Tests live in [`tests/`](tests/). Cross-layer futures routing tests live in
[`../../../tests/contract/`](../../../tests/contract/).
