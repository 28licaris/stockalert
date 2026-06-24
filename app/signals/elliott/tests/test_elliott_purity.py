"""Purity gate: app/signals/elliott/ must stay a pure, lift-out-friendly
package — no imports from app.db / app.providers / app.services. There is no
existing AST gate to reuse, so we add one here (it polices the lift-out
contract the spec promises)."""
from __future__ import annotations

import ast
import pathlib

FORBIDDEN = ("app.db", "app.providers", "app.services")
PKG = pathlib.Path(__file__).resolve().parents[1] / "app" / "signals" / "elliott"


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_elliott_package_is_pure():
    offenders: dict[str, set[str]] = {}
    for py in PKG.glob("*.py"):
        bad = {m for m in _imports(py) if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)}
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"forbidden imports in elliott package: {offenders}"
