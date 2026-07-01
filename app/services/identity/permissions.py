"""Role/allowlist → permission derivation.

Kept separate from `entitlements.py` (which maps *subscription* state to
feature entitlements) because permissions describe *operator authority*,
not billing. Two independent grants of `operator.access`:

  - the user's email is in the ``ADMIN_EMAILS`` allowlist (bootstraps the
    founder-admin with no DB write; survives identity-DB rebuilds), or
  - the user's membership role is ``Role.ADMIN`` (dynamic grants later).
"""
from __future__ import annotations

from app.services.identity.schemas import Role

OPERATOR_ACCESS = "operator.access"


def _admin_allowlist() -> set[str]:
    from app.config import settings

    return {
        e.strip().lower()
        for e in (settings.admin_emails or "").split(",")
        if e.strip()
    }


def permissions_for(role: Role, email: str | None) -> frozenset[str]:
    """Return the permission set for an authenticated principal."""
    is_admin = role == Role.ADMIN or (email or "").strip().lower() in _admin_allowlist()
    return frozenset({OPERATOR_ACCESS}) if is_admin else frozenset()
