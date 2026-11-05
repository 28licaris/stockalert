# infra/

CloudFormation and box configuration. **No CDK, no Terraform** (repo rule,
[`../CLAUDE.md`](../CLAUDE.md) → Infra). Every stack is parameterised by
`EnvironmentName`; dev and prod are two *instantiations* of the same
templates, not two copies.

| Dir | Owns | Deploy |
|---|---|---|
| [`auth/`](auth/README.md) | Cognito user pool + app client per environment (`stockalert-auth-<env>`) | `aws cloudformation deploy … --template-file infra/auth/cognito.yaml` |
| [`dev/`](dev/README.md) | The application box: one Graviton EC2 running Docker Compose (`caddy`, `api`, `worker`, `clickhouse`, `postgres`), plus ops bucket, ECR, logs, snapshots, alerts, health check | `stack.yaml` via CloudFormation; files via `scripts/deploy/release.sh` |

Spec and decisions: [`docs/operations/deployment_dev.md`](../docs/operations/deployment_dev.md).
Mac-side tooling: [`scripts/deploy/`](../scripts/deploy/README.md).

## dev vs prod

What varies per environment is **data, not structure**:

| Varies | Where it lives |
|---|---|
| Resource names, SSM path `/stockalert/<env>/`, log group, tags | `stack.yaml` parameter `EnvironmentName` |
| Instance size, root volume | `stack.yaml` parameters (`InstanceType`, `RootVolumeSizeGiB`) |
| Hostname, Cognito pool/client ids, operator emails, flags, bucket names | `<env>/app.env` (committed, non-secret) |
| Secrets | SSM Parameter Store `SecureString`s under `/stockalert/<env>/` (never in git) |
| Container image | one ECR repository `stockalert/app` for every environment; a release pins `:<sha>` |

`infra/dev/` is the only box layout today. Prod (spec §11) is a second
stack from the **same** `stack.yaml` (`EnvironmentName=prod`,
`CreateEcrRepository=false`) plus an `infra/prod/` holding its own `app.env`
and otherwise the same `docker-compose.yml`, `Caddyfile`, `clickhouse/`,
`systemd/`, `cron/`, `bin/` and `bootstrap.sh` — those files read
`STOCKALERT_ENV` and the rendered `.env`, and contain nothing dev-specific.
`scripts/deploy/release.sh <env>` syncs `infra/<env>/`. Where prod needs a
different *shape* (RDS instead of the postgres container, KMS token cipher)
that is a spec change first, per
[`../docs/standards/engagement.md`](../docs/standards/engagement.md).
