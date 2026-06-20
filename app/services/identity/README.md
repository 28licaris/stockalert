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
