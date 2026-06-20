"""Amazon Cognito OIDC adapter.

The adapter owns provider-specific HTTP and JWT validation. Callers receive a
validated Pydantic identity, never untrusted token claims.
"""
from __future__ import annotations

import hmac
import time
import asyncio
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK
from pydantic import ValidationError

from app.services.identity.contract import IdentityProviderError
from app.services.identity.schemas import (
    CognitoOAuthConfig,
    CognitoTokenSet,
    ExternalIdentityClaim,
)


class CognitoIdentityProvider:
    def __init__(
        self,
        *,
        config: CognitoOAuthConfig,
        http_client: httpx.AsyncClient | None = None,
        jwks_ttl_seconds: int = 3600,
    ) -> None:
        self._config = config
        self._http_client = http_client
        self._jwks_ttl_seconds = jwks_ttl_seconds
        self._jwks: dict[str, PyJWK] = {}
        self._jwks_loaded_at = 0.0
        self._jwks_lock = asyncio.Lock()

    @classmethod
    def from_settings(cls) -> "CognitoIdentityProvider":
        from app.config import settings

        secret = settings.cognito_client_secret.strip()
        return cls(
            config=CognitoOAuthConfig(
                domain=settings.cognito_domain,
                issuer_url=settings.cognito_issuer_url,
                client_id=settings.cognito_client_id,
                client_secret=secret or None,
            )
        )

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
        identity_provider: str | None = None,
    ) -> str:
        params = {
                "response_type": "code",
                "client_id": self._config.client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(self._config.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge_method": "S256",
                "code_challenge": code_challenge,
        }
        if identity_provider:
            params["identity_provider"] = identity_provider
        query = urlencode(params)
        return f"{self._config.domain}/oauth2/authorize?{query}"

    async def exchange_code(
        self,
        code: str,
        *,
        redirect_uri: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> CognitoTokenSet:
        form = {
            "grant_type": "authorization_code",
            "client_id": self._config.client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        auth: httpx.BasicAuth | None = None
        if self._config.client_secret is not None:
            auth = httpx.BasicAuth(
                self._config.client_id,
                self._config.client_secret.get_secret_value(),
            )
        response = await self._request(
            "POST",
            f"{self._config.domain}/oauth2/token",
            data=form,
            auth=auth,
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise IdentityProviderError(
                "token_exchange_failed",
                f"Cognito token exchange returned HTTP {response.status_code}",
            )
        try:
            payload = response.json()
            id_token = str(payload["id_token"])
            access_token = str(payload["access_token"])
            expires_in = int(payload["expires_in"])
        except (KeyError, TypeError, ValueError) as exc:
            raise IdentityProviderError(
                "invalid_token_response", "Cognito returned an invalid token response"
            ) from exc

        claims = await self._validate_id_token(id_token, expected_nonce=expected_nonce)
        verified_raw = claims.get("email_verified", False)
        email_verified = verified_raw is True or str(verified_raw).lower() == "true"
        email = str(claims.get("email") or "")
        display_name = str(
            claims.get("name")
            or claims.get("cognito:username")
            or email.partition("@")[0]
        )
        try:
            identity = ExternalIdentityClaim(
                provider="cognito",
                subject=str(claims["sub"]),
                email=email,
                email_verified=email_verified,
                display_name=display_name,
            )
            return CognitoTokenSet(
                identity=identity,
                access_token=access_token,
                id_token=id_token,
                refresh_token=payload.get("refresh_token"),
                expires_in=expires_in,
            )
        except (KeyError, ValidationError) as exc:
            raise IdentityProviderError(
                "invalid_identity_claims", "Cognito ID token lacks required claims"
            ) from exc

    def logout_url(self, *, logout_uri: str) -> str:
        return f"{self._config.domain}/logout?{urlencode({'client_id': self._config.client_id, 'logout_uri': logout_uri})}"

    async def _validate_id_token(
        self, id_token: str, *, expected_nonce: str
    ) -> dict[str, object]:
        try:
            header = jwt.get_unverified_header(id_token)
            kid = str(header["kid"])
            key = await self._get_signing_key(kid)
            claims = jwt.decode(
                id_token,
                key=key.key,
                algorithms=["RS256"],
                audience=self._config.client_id,
                issuer=self._config.issuer_url,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "token_use"]},
            )
        except (KeyError, jwt.PyJWTError) as exc:
            raise IdentityProviderError(
                "invalid_id_token", "Cognito ID token validation failed"
            ) from exc

        if claims.get("token_use") != "id":
            raise IdentityProviderError("invalid_token_use", "Expected a Cognito ID token")
        nonce = str(claims.get("nonce") or "")
        if not hmac.compare_digest(nonce, expected_nonce):
            raise IdentityProviderError("invalid_nonce", "Cognito nonce validation failed")
        return claims

    async def _get_signing_key(self, kid: str) -> PyJWK:
        now = time.monotonic()
        key = self._jwks.get(kid)
        cache_fresh = now - self._jwks_loaded_at < self._jwks_ttl_seconds
        if key is not None and cache_fresh:
            return key

        async with self._jwks_lock:
            # Recheck after acquiring: another request may have refreshed it.
            now = time.monotonic()
            key = self._jwks.get(kid)
            cache_fresh = now - self._jwks_loaded_at < self._jwks_ttl_seconds
            if key is None or not cache_fresh:
                await self._refresh_jwks(now)
                key = self._jwks.get(kid)
            if key is None:
                raise IdentityProviderError(
                    "unknown_signing_key", "Cognito signing key was not found"
                )
            return key

    async def _refresh_jwks(self, loaded_at: float) -> None:
        response = await self._request(
            "GET", f"{self._config.issuer_url}/.well-known/jwks.json"
        )
        if response.status_code != 200:
            raise IdentityProviderError(
                "jwks_unavailable",
                f"Cognito JWKS returned HTTP {response.status_code}",
            )
        try:
            keys = response.json()["keys"]
            self._jwks = {str(raw["kid"]): PyJWK.from_dict(raw) for raw in keys}
            self._jwks_loaded_at = loaded_at
        except (KeyError, TypeError, ValueError, jwt.PyJWTError) as exc:
            raise IdentityProviderError(
                "invalid_jwks", "Cognito returned an invalid JWKS document"
            ) from exc

    async def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        try:
            if self._http_client is not None:
                return await self._http_client.request(method, url, **kwargs)
            async with httpx.AsyncClient(timeout=10.0) as client:
                return await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise IdentityProviderError(
                "provider_unavailable", "Cognito request failed"
            ) from exc
