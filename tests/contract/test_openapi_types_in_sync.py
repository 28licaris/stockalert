"""Guard: the generated frontend types must cover the live OpenAPI schema.

`npm run codegen` points `openapi-typescript` at a **running** API on
:8000, so it is easy to skip — and it was skipped. The committed
`frontend/src/api/types.gen.ts` went months stale, missing
`/api/v1/ppl/*` and `/api/v1/health/freshness` entirely, which is what
lets hand-written duplicate types in `queries.ts` silently diverge from
the backend: TypeScript still compiles, the data is just wrong.

This test needs no server and no node — it builds the schema in-process
via `app.openapi()` and checks every schema name appears in the
generated file.

It deliberately checks *names*, not full shapes. A name-level check is
cheap, has no false positives, and catches the failure that actually
happened (a whole endpoint missing). Verifying field-level drift would
mean reimplementing openapi-typescript here; if that becomes necessary,
run the real generator in CI and diff instead.

**When this fails**, regenerate rather than editing the generated file:

    poetry run python -c "import json, app.main_api as m; \
print(json.dumps(m.app.openapi()))" > /tmp/openapi.json
    cd frontend && npx openapi-typescript /tmp/openapi.json -o src/api/types.gen.ts
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_TYPES_GEN = Path(__file__).resolve().parents[2] / "frontend/src/api/types.gen.ts"

# Schema names sit at exactly 8-space indent inside `components.schemas`.
# Two emitted forms: `Name: {` for objects, `Name: "a" | "b";` for enums.
_SCHEMA_KEY = re.compile(r"^ {8}([A-Za-z_][A-Za-z0-9_]*):", re.MULTILINE)


def _generated_schema_names() -> set[str]:
    return set(_SCHEMA_KEY.findall(_TYPES_GEN.read_text()))


def _openapi_schema_names() -> set[str]:
    from app.main_api import app

    return set(app.openapi().get("components", {}).get("schemas", {}))


@pytest.mark.skipif(not _TYPES_GEN.exists(), reason="frontend/ not checked out")
def test_every_openapi_schema_is_in_the_generated_types() -> None:
    missing = sorted(_openapi_schema_names() - _generated_schema_names())
    assert not missing, (
        f"{len(missing)} OpenAPI schema(s) missing from types.gen.ts — it is "
        f"stale. Regenerate it (see this module's docstring). Missing: "
        f"{missing}"
    )


@pytest.mark.skipif(not _TYPES_GEN.exists(), reason="frontend/ not checked out")
def test_the_guard_can_actually_see_both_emitted_forms() -> None:
    """Self-check so a broken regex cannot make the guard above vacuous.

    An earlier version matched only `Name: {` and silently reported
    three enums as missing. If the regex regresses to matching nothing,
    the test above would pass for the wrong reason.
    """
    names = _generated_schema_names()
    assert len(names) > 50, f"regex matched only {len(names)} names — likely broken"
    # An object-form schema and an enum-form schema must both be found.
    assert "AdjustedBar" in names, "object-form schema not matched"
    assert "BarSource" in names, "enum-form schema not matched"
