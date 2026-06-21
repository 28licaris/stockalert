# Customer Identity Service

Owns StockAlert customer accounts, tenants, memberships, and opaque application
sessions. Credentials, password reset codes, and MFA secrets belong to the
external identity provider (Amazon Cognito in production), never this service.

## Public contract

- `schemas.py` contains every Pydantic DTO crossing the service boundary.
- `contract.py` contains the repository, provider, and service Protocols.
- Other services must not import `service.py`, `repository.py`, or `models.py`.

## Internal modules

| File | Responsibility |
|---|---|
| `service.py` | Provider-independent account/session orchestration |
| `auth_service.py` | OAuth state/PKCE/callback orchestration |
| `cognito.py` | Cognito endpoints, token exchange, JWKS/JWT validation |
| `repository.py` | Transactional PostgreSQL implementation |
| `models.py` | Private SQLAlchemy persistence models |
| `security.py` | Opaque-token generation and one-way lookup hashing |

The API layer will translate Cognito callbacks into
`ExternalIdentityClaim`, call `IdentityService`, and place only the raw opaque
session token in a secure HTTP-only cookie. PostgreSQL stores its SHA-256
lookup digest, never the raw token.

Browser routes are `/auth/login`, `/auth/callback`, and `/auth/logout`.
`/api/v1/customer/me` proves the authenticated customer boundary;
`/api/v1/admin/me` additionally requires the explicit `operator.access`
permission and therefore denies ordinary tenant admins.

Authenticated customers manage their own active application sessions through
`GET /api/v1/customer/sessions`,
`DELETE /api/v1/customer/sessions/{session_id}`, and
`POST /api/v1/customer/sessions/revoke-others`. Session mutations require the
double-submit CSRF token and repository queries always scope session IDs by the
authenticated user and tenant. The current session is revoked only through the
normal `/auth/logout` flow so cookies and the Cognito browser session are
cleared together.

Security-sensitive account activity is stored in PostgreSQL and exposed only
to the owning user and tenant at `GET /api/v1/customer/security-events`.
Successful login, logout, and session-revocation events contain opaque IDs and
timestamps only; credentials, tokens, IP addresses, and provider payloads are
never written to the audit trail.

## TOTP MFA

Authenticated customers enroll a time-based one-time passcode through
`GET /api/v1/customer/mfa` (status),
`POST /api/v1/customer/mfa/enrollment` (begin — returns a secret + `otpauth://`
URI for a QR code), and `POST /api/v1/customer/mfa/enrollment/verify` (confirm
the first code and set the Cognito TOTP preference). Enabling MFA writes an
`mfa_enabled` security event.

Cognito's managed login cannot enroll *optional* MFA, and the TOTP admin APIs
require a user access token carrying the `aws.cognito.signin.user.admin` scope.
The app does not keep provider tokens in the browser, so each session encrypts
that access token at rest (`sessions.provider_session_ciphertext`) via
`provider_session.py`:

- `local` — AES-GCM keyed from the confidential Cognito client secret
  (development only).
- `kms` — an AWS KMS customer-managed key (production); region is taken from the
  key ARN when supplied.

Set `AUTH_PROVIDER_TOKEN_CIPHER` accordingly; it defaults to `disabled`, which
fails MFA and login closed until a cipher is chosen. Tokens are short-lived, so
an expired session returns `reauthentication_required` and the UI sends the user
back through Cognito before MFA changes are allowed.

**Production checklist**

1. Deploy the updated `infra/auth/cognito.yaml` so the user pool client allows
   the `aws.cognito.signin.user.admin` scope (the pool already enables
   `SOFTWARE_TOKEN_MFA` with `MfaConfiguration: OPTIONAL`). Existing deployments
   must re-apply the stack for the new scope to take effect.
2. Set `AUTH_PROVIDER_TOKEN_CIPHER=kms` and `AUTH_PROVIDER_TOKEN_KMS_KEY_ID` to a
   customer-managed key ARN.
3. Grant the app role `kms:Encrypt` + `kms:Decrypt` on that key and
   `cognito-idp:GetUser`, `AssociateSoftwareToken`, `VerifySoftwareToken`, and
   `SetUserMFAPreference` on the user pool.

Federated (e.g. Google) sessions report `supported: false` — MFA for those
users is owned by the upstream identity provider, not Cognito.

## Local database

```bash
docker compose --profile identity up -d postgres
IDENTITY_DATABASE_URL=postgresql+psycopg://stockalert:stockalert_dev@localhost:5432/stockalert_identity \
  poetry run alembic upgrade head
```

The Docker credentials are development-only. Production loads its RDS URL from
the approved secret store.

## Tests

```bash
poetry run pytest tests/test_identity_contracts.py tests/test_identity_service.py
```

PostgreSQL repository integration tests use `TEST_IDENTITY_DATABASE_URL` and
are marked `integration`; they must point at a disposable test database.

```bash
docker compose --profile identity-test up -d postgres-test
TEST_IDENTITY_DATABASE_URL=postgresql+psycopg://stockalert:stockalert_test@localhost:5433/stockalert_identity_test \
  poetry run pytest tests/integration/test_identity_postgres.py
```
