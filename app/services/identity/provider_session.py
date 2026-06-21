"""Authenticated encryption for short-lived Cognito session material."""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from hashlib import sha256
from typing import Protocol

import boto3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, SecretStr


_AAD = b"stockalert:provider-session:v1"


class ProviderSessionMaterial(BaseModel):
    access_token: SecretStr
    expires_at: datetime
    source_provider: str


class ProviderSessionCipher(Protocol):
    def encrypt(self, material: ProviderSessionMaterial) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> ProviderSessionMaterial: ...


def _serialize(material: ProviderSessionMaterial) -> bytes:
    payload = material.model_dump(mode="json")
    payload["access_token"] = material.access_token.get_secret_value()
    return json.dumps(payload, separators=(",", ":")).encode()


class LocalAesGcmProviderSessionCipher:
    """Development-only cipher derived from the confidential app client secret."""

    def __init__(self, client_secret: str) -> None:
        if not client_secret:
            raise ValueError("Cognito client secret is required for local token encryption")
        self._cipher = AESGCM(sha256(_AAD + client_secret.encode()).digest())

    def encrypt(self, material: ProviderSessionMaterial) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, _serialize(material), _AAD)

    def decrypt(self, ciphertext: bytes) -> ProviderSessionMaterial:
        if len(ciphertext) < 29:
            raise ValueError("provider session ciphertext is invalid")
        raw = self._cipher.decrypt(ciphertext[:12], ciphertext[12:], _AAD)
        return ProviderSessionMaterial.model_validate_json(raw)


def _kms_region(key_id: str, fallback: str) -> str:
    """Prefer the region embedded in a key ARN so the client targets the
    key's home region; fall back to the configured region for key-id/alias forms.
    """
    if key_id.startswith("arn:"):
        parts = key_id.split(":")
        if len(parts) >= 4 and parts[3]:
            return parts[3]
    if not fallback:
        raise ValueError(
            "AUTH_PROVIDER_TOKEN_KMS_REGION is required when the key is not a full ARN"
        )
    return fallback


class KmsProviderSessionCipher:
    """Production cipher backed by an AWS KMS customer-managed key."""

    def __init__(self, *, key_id: str, region: str) -> None:
        if not key_id:
            raise ValueError("AUTH_PROVIDER_TOKEN_KMS_KEY_ID is required")
        self._key_id = key_id
        self._client = boto3.client("kms", region_name=_kms_region(key_id, region))

    def encrypt(self, material: ProviderSessionMaterial) -> bytes:
        response = self._client.encrypt(
            KeyId=self._key_id,
            Plaintext=_serialize(material),
            EncryptionContext={"purpose": "stockalert-provider-session"},
        )
        return bytes(response["CiphertextBlob"])

    def decrypt(self, ciphertext: bytes) -> ProviderSessionMaterial:
        response = self._client.decrypt(
            CiphertextBlob=ciphertext,
            EncryptionContext={"purpose": "stockalert-provider-session"},
        )
        return ProviderSessionMaterial.model_validate_json(bytes(response["Plaintext"]))


def provider_source_from_id_claims(claims: dict[str, object]) -> str:
    identities = claims.get("identities")
    if isinstance(identities, list) and identities:
        identity = identities[0]
        if isinstance(identity, dict):
            return str(identity.get("providerName") or "cognito").casefold()
    return "cognito"
