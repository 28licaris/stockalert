"""System-prompt registry.

The prompt itself is a versioned markdown file shipped with the
package (`v1.md`, `v2.md`, …). The registry exposes:

  - `current()` — the (version, text, hash) tuple of the active prompt
  - `load(version)` — a specific version by name
  - `versions()` — all bundled versions

The hash is part of the response-cache key (see `cache.py`). A
prompt change therefore invalidates the cache automatically — there's
no way to silently change behavior while keeping cached responses.

Why bundle the file in the package vs. load from disk dynamically:
ships with the wheel, no I/O at request time after the first hit,
and the hash is stable across deploys of the same commit.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Final

_CURRENT_VERSION: Final[str] = "v1"


@dataclass(frozen=True, slots=True)
class SystemPrompt:
    """A loaded system prompt + its identity for cache-keying."""

    version: str
    text: str
    sha256: str

    @property
    def short_hash(self) -> str:
        """First 12 chars of the sha256 — enough for log lines / debug."""
        return self.sha256[:12]


@lru_cache(maxsize=8)
def load(version: str = _CURRENT_VERSION) -> SystemPrompt:
    """Return the bundled system prompt for `version`.

    Raises `FileNotFoundError` if the version doesn't exist; this is
    a programmer error, never a runtime data condition.
    """
    resource = files(__package__).joinpath(f"{version}.md")
    text = resource.read_text(encoding="utf-8")
    sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SystemPrompt(version=version, text=text, sha256=sha256)


def current() -> SystemPrompt:
    """The active system prompt — what every new turn uses by default."""
    return load(_CURRENT_VERSION)


def versions() -> list[str]:
    """Discoverable list of bundled prompt versions."""
    return sorted(
        p.stem
        for p in files(__package__).iterdir()  # type: ignore[union-attr]
        if p.name.endswith(".md") and p.name.startswith("v")
    )


__all__ = ["SystemPrompt", "current", "load", "versions"]
