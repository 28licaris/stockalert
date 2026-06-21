# Identity PostgreSQL migrations

Alembic is the only supported mechanism for changing the customer identity
database schema. Migrations import the private SQLAlchemy metadata from
`app.services.identity.models`; application startup never mutates schemas.

```bash
IDENTITY_DATABASE_URL=postgresql+psycopg://stockalert:stockalert_dev@localhost:5432/stockalert_identity \
  poetry run alembic upgrade head
```
