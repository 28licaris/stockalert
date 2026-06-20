"""Cognito OIDC adapter tests with locally signed JWTs and mock HTTP."""
from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.identity.cognito import CognitoIdentityProvider
from app.services.identity.contract import IdentityProviderError
from app.services.identity.schemas import CognitoOAuthConfig


ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST"
DOMAIN = "https://stockalert-test.auth.us-east-1.amazoncognito.com"
CLIENT_ID = "test-client-id"


def _key_material() -> tuple[object, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
    return private_key, jwk


def _id_token(private_key: object, *, nonce: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "cognito-subject",
            "email": "trader@example.com",
            "email_verified": True,
            "name": "Test Trader",
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 300,
            "token_use": "id",
            "nonce": nonce,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def test_authorization_url_uses_code_pkce_state_and_nonce() -> None:
    provider = CognitoIdentityProvider(
        config=CognitoOAuthConfig(
            domain=DOMAIN, issuer_url=ISSUER, client_id=CLIENT_ID
        )
    )
    url = provider.authorization_url(
        state="state-value",
        nonce="nonce-value",
        code_challenge="challenge-value",
        redirect_uri="http://localhost:8000/auth/callback",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{DOMAIN}/oauth2/authorize"
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["state-value"]
    assert query["nonce"] == ["nonce-value"]


def test_authorization_url_supports_signup_hint_and_prompt() -> None:
    provider = CognitoIdentityProvider(
        config=CognitoOAuthConfig(
            domain=DOMAIN, issuer_url=ISSUER, client_id=CLIENT_ID
        )
    )
    url = provider.authorization_url(
        state="state-value",
        nonce="nonce-value",
        code_challenge="challenge-value",
        redirect_uri="http://localhost:8000/auth/callback",
        screen_hint="signup",
        prompt="login",
    )
    query = parse_qs(urlparse(url).query)
    assert query["screen_hint"] == ["signup"]
    assert query["prompt"] == ["login"]


def test_password_reset_url_targets_managed_reset_page() -> None:
    provider = CognitoIdentityProvider(
        config=CognitoOAuthConfig(
            domain=DOMAIN, issuer_url=ISSUER, client_id=CLIENT_ID
        )
    )
    url = provider.password_reset_url(
        redirect_uri="http://localhost:8000/auth/callback"
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        f"{DOMAIN}/forgotPassword"
    )
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == ["http://localhost:8000/auth/callback"]


def test_logout_url_targets_allowed_signout_page() -> None:
    provider = CognitoIdentityProvider(
        config=CognitoOAuthConfig(
            domain=DOMAIN, issuer_url=ISSUER, client_id=CLIENT_ID
        )
    )

    url = provider.logout_url(logout_uri="http://localhost:8000/app/login")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{DOMAIN}/logout"
    assert query == {
        "client_id": [CLIENT_ID],
        "logout_uri": ["http://localhost:8000/app/login"],
    }


@pytest.mark.asyncio
async def test_exchange_code_validates_jwt_and_returns_identity() -> None:
    private_key, public_jwk = _key_material()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/jwks.json"):
            return httpx.Response(200, json={"keys": [public_jwk]})
        assert request.url.path == "/oauth2/token"
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["authorization_code"]
        assert form["code_verifier"] == ["verifier-value"]
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "id_token": _id_token(private_key, nonce="expected-nonce"),
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CognitoIdentityProvider(
            config=CognitoOAuthConfig(
                domain=DOMAIN,
                issuer_url=ISSUER,
                client_id=CLIENT_ID,
                client_secret="client-secret",
            ),
            http_client=client,
        )
        result = await provider.exchange_code(
            "authorization-code",
            redirect_uri="http://localhost:8000/auth/callback",
            code_verifier="verifier-value",
            expected_nonce="expected-nonce",
        )

    assert result.identity.subject == "cognito-subject"
    assert result.identity.email == "trader@example.com"
    assert result.identity.email_verified is True
    assert result.access_token.get_secret_value() == "access-token"


@pytest.mark.asyncio
async def test_exchange_code_rejects_nonce_mismatch() -> None:
    private_key, public_jwk = _key_material()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/jwks.json"):
            return httpx.Response(200, json={"keys": [public_jwk]})
        return httpx.Response(
            200,
            json={
                "id_token": _id_token(private_key, nonce="wrong-nonce"),
                "access_token": "access-token",
                "expires_in": 3600,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CognitoIdentityProvider(
            config=CognitoOAuthConfig(
                domain=DOMAIN, issuer_url=ISSUER, client_id=CLIENT_ID
            ),
            http_client=client,
        )
        with pytest.raises(IdentityProviderError) as exc_info:
            await provider.exchange_code(
                "authorization-code",
                redirect_uri="http://localhost:8000/auth/callback",
                code_verifier="verifier-value",
                expected_nonce="expected-nonce",
            )
    assert exc_info.value.code == "invalid_nonce"
