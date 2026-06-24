# Domain services

Domain modules for the modular monolith. A service owns one business capability
and should remain liftable into a separate process.

Before editing a service, read
[`../../docs/standards/service_modules.md`](../../docs/standards/service_modules.md).
Cross-service callers use public schemas, contracts, or package exports—not
another module's implementation internals. Shared Iceberg infrastructure that
does not form a domain lives directly in this package.

Every child module has a README and colocated unit tests. Cross-service behavior
belongs in [`../../tests/contract/`](../../tests/contract/); live storage and
provider behavior belongs in [`../../tests/integration/`](../../tests/integration/).
