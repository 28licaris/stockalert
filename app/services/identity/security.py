"""Small, pure cryptographic helpers for opaque application sessions."""
from __future__ import annotations

import hashlib
import secrets
import base64
from urllib.parse import urlsplit


SESSION_TOKEN_BYTES = 32
OAUTH_STATE_BYTES = 32
OAUTH_NONCE_BYTES = 32
PKCE_VERIFIER_BYTES = 64


def generate_session_token() -> str:
    """Return at least 256 bits of URL-safe session entropy."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """Return the non-reversible lookup digest stored in PostgreSQL."""
    if not token:
        raise ValueError("session token is required")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(OAUTH_STATE_BYTES)


def generate_oauth_nonce() -> str:
    return secrets.token_urlsafe(OAUTH_NONCE_BYTES)


def generate_pkce_verifier() -> str:
    return secrets.token_urlsafe(PKCE_VERIFIER_BYTES)


def pkce_s256_challenge(verifier: str) -> str:
    if not verifier:
        raise ValueError("PKCE verifier is required")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def safe_return_path(value: str | None, *, default: str = "/app/") -> str:
    """Allow same-origin absolute paths only; reject scheme-relative URLs."""
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(char) < 0x20 for char in value)
    ):
        return default
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return default
    return value
