"""
Universal provider-adjustment probe registry.

Each provider's probe is a class implementing `ProviderAdjustmentProbe`
(see `base.py`). Providers self-register on import via the
`@register_probe` decorator. `scripts/probe_provider_adjustment.py`
loads every registered probe and runs them against a `ProbeSpec`.

**Adding a new provider** (the universal pattern):

1. Create `app/services/silver/probes/<provider>.py`.
2. Define a class with `provider_name: str` and `async def probe(spec)`.
3. Decorate the class with `@register_probe("<provider>")`.
4. Import it from this `__init__.py` so it loads when the registry
   is queried.

That's it. Re-run `scripts/probe_provider_adjustment.py` and the new
provider appears in the output. **No changes to the runner, no
changes to the silver build.**

See [README.md](README.md) for the full onboarding checklist
including the rules about whether the new provider also needs
corp-actions ingest.
"""
from __future__ import annotations

from typing import Callable, Type, TypeVar

from app.services.silver.probes.base import (
    DEFAULT_PROBE_SPEC,
    KNOWN_PROBES,
    ProbeResult,
    ProbeSpec,
    ProviderAdjustmentProbe,
)

# Provider-name → probe-class registry.
_PROBE_REGISTRY: dict[str, type] = {}


T = TypeVar("T")


def register_probe(name: str) -> Callable[[Type[T]], Type[T]]:
    """Class decorator: register a probe class for `name`.

    Usage:
        @register_probe("polygon")
        class PolygonAdjustmentProbe:
            provider_name = "polygon"
            async def probe(self, spec): ...

    Re-registering the same name raises (intentional — fail fast on
    accidental duplicate registration).
    """
    def decorator(cls: Type[T]) -> Type[T]:
        if name in _PROBE_REGISTRY:
            raise ValueError(
                f"Probe '{name}' already registered. Pick a unique name "
                f"or remove the duplicate."
            )
        _PROBE_REGISTRY[name] = cls
        return cls
    return decorator


def list_registered_probes() -> list[str]:
    """Sorted list of registered probe names."""
    return sorted(_PROBE_REGISTRY)


def build_all_probes() -> list[ProviderAdjustmentProbe]:
    """Instantiate every registered probe. Order: sorted by name."""
    return [_PROBE_REGISTRY[name]() for name in sorted(_PROBE_REGISTRY)]


# Import all provider probe modules so their @register_probe decorators
# run. The runner doesn't have to know about specific providers.
#
# When adding a new provider, add its import here.
from app.services.silver.probes import polygon  # noqa: F401,E402
from app.services.silver.probes import schwab   # noqa: F401,E402


__all__ = [
    "DEFAULT_PROBE_SPEC",
    "KNOWN_PROBES",
    "ProbeResult",
    "ProbeSpec",
    "ProviderAdjustmentProbe",
    "build_all_probes",
    "list_registered_probes",
    "register_probe",
]
