# infra/dev/ — the StockAlert box

Everything that lands on the EC2 instance at `/opt/stockalert/`, plus the
CloudFormation stack that creates the instance and its surroundings.
Spec: [`docs/operations/deployment_dev.md`](../../docs/operations/deployment_dev.md)
(§3 containers/flags/routing, §6 build+release, §7 resources, §13 decisions).
Mac-side commands: [`scripts/deploy/`](../../scripts/deploy/README.md).

## Files

| File | On the box | Purpose |
|---|---|---|
| `stack.yaml` | — | CloudFormation: SG (80/443 only, no SSH), IAM role + instance profile (§7 policy), `t4g.large` AL2023 arm64 via launch template (IMDSv2, 100 GiB gp3), EIP, DLM daily snapshot keep 7, ops bucket (`pg_dump/` 30-day lifecycle), ECR `stockalert/app` keep-last-10, log group 14 d, SNS topic, Route 53 health check + alarm, disk alarm |
| `docker-compose.yml` | `/opt/stockalert/docker-compose.yml` | `caddy`, `api`, `worker`, `clickhouse`, `postgres`. `stream-flags` / `jobs-flags` anchors are the api↔worker difference. `awslogs` driver, `restart: unless-stopped`, worker `cpus 1.0` / `2500m`, clickhouse `3g` |
| `Caddyfile` | `/opt/stockalert/Caddyfile` | TLS (Let's Encrypt, automatic for the `DEV_HOST` site), routing table from §3, 403 for `/mcp*`, `/docs`, `/redoc`, `/openapi.json` |
| `clickhouse/config.d/system_logs.xml` | `…/clickhouse/config.d/` | 7-day TTL on `query_log`/`part_log`; `trace_log`/`query_thread_log` removed |
| `app.env` | `/opt/stockalert/app.env` | Committed non-secret config: region, bucket, Glue DBs, Cognito ids, schedules, flags shared by both roles |
| `systemd/stockalert-env.service` | `/etc/systemd/system/` | oneshot: `bin/render_env.sh` (SSM → `.env`) |
| `systemd/stockalert.service` | `/etc/systemd/system/` | `docker compose up -d` after the env unit; `Restart=on-failure` (`always` is invalid for oneshot) |
| `cron/stockalert` | `/etc/cron.d/stockalert` | 04:00 UTC `pg_backup.sh`; 23:30 UTC paper pipeline **commented out until cutover M4** |
| `bin/render_env.sh` | `/opt/stockalert/bin/` | `app.env` + `release.env` + SSM `/stockalert/<env>/*` + derived URLs → `.env` (0600, atomic). Fails loud on any missing required key |
| `bin/release.sh` | `/opt/stockalert/bin/` | `<image>` → `release.env`, render, ECR login, pull, datastores up, `alembic upgrade head` in a one-off `api`, `up -d`, `/health` inside and via Caddy |
| `bin/pg_backup.sh` | `/opt/stockalert/bin/` | `pg_dump -Fc` → `s3://<ops>/pg_dump/`, size verified cross-side |
| `bootstrap.sh` | `/opt/stockalert/bootstrap.sh` | First boot: Docker + Compose plugin (pinned), 2 GiB swapfile, cronie, CloudWatch agent (disk/mem), systemd units enabled |

## How the pieces connect

```
stack.yaml ──creates──► EC2 ── user-data shim ──► waits for s3://<ops>/infra/bootstrap.sh
                                                   │  (scripts/deploy/release.sh syncs infra/dev/ there)
                                                   ▼
                                           bootstrap.sh  (docker, swap, cron, CW agent, units enabled)
                                                   ▼
  scripts/deploy/release.sh <env> <sha> ─SSM─► bin/release.sh <image>
                                                   ├─ release.env  APP_IMAGE=<ecr>/stockalert/app:<sha>
                                                   ├─ bin/render_env.sh → .env  (app.env + release.env + SSM + derived)
                                                   ├─ compose pull · up clickhouse postgres · run api alembic upgrade head
                                                   └─ compose up -d · /health (in-container, then https://DEV_HOST/health)
  reboot ─► stockalert-env.service ─► stockalert.service (compose up -d) ─► containers (restart: unless-stopped)
```

**`.env` is rendered, never edited.** Order, later wins: `app.env` →
`release.env` → SSM (leaf name upper-cased: `schwab_client_id` →
`SCHWAB_CLIENT_ID`) → derived (`IDENTITY_DATABASE_URL` from
`POSTGRES_PASSWORD`; `COGNITO_REDIRECT_URI`, `COGNITO_LOGOUT_URI`,
`BILLING_RETURN_URL` from `DEV_HOST`). `DEV_HOST` empty in `app.env` means
`app.<eip-with-dashes>.sslip.io` derived from IMDS (§13: no domain yet); set
it in `app.env` when a domain exists. Per-role flags (`STREAM_PROVIDER`,
`*_ENABLED`) are set per container in the compose file and override `.env`.

**SSM parameters** (`/stockalert/<env>/`, all `SecureString`, via
`scripts/deploy/put_param.sh`): required — `schwab_client_id`,
`schwab_client_secret`, `schwab_refresh_token`, `polygon_api_key`,
`cognito_client_secret`, `postgres_password` (URL-safe chars only),
`clickhouse_password`; optional — `stripe_secret_key`, `stripe_webhook_secret`,
`stripe_price_monthly`, `stripe_price_annual`, `anthropic_api_key`,
`bls_api_key`, `bea_api_key`. `render_env.sh` names any missing required key.

## First deploy (M2 in the spec's cutover plan)

```bash
# 0. default VPC + a public subnet
aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text --profile stockalert-admin
aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc> Name=default-for-az,Values=true --query 'Subnets[0].SubnetId' --output text --profile stockalert-admin

# 1. stack (≈5 min; confirm the SNS subscription email it sends)
aws cloudformation deploy --profile stockalert-admin --region us-east-1 \
  --stack-name stockalert-dev --template-file infra/dev/stack.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides EnvironmentName=dev LakeBucket=<STOCK_LAKE_BUCKET> \
    AlertEmail=<operator email> CognitoUserPoolId=us-east-1_NxTarvrk4 VpcId=<vpc> SubnetId=<subnet>

# 2. secrets
printf '%s' "$VALUE" | scripts/deploy/put_param.sh dev <name>     # each name above
scripts/deploy/push_schwab_token.sh dev --no-auth                  # or without --no-auth to re-auth first

# 3. image + release (the instance is waiting for this sync to finish booting)
scripts/deploy/build_push.sh dev
scripts/deploy/release.sh dev <sha>

# 4. Cognito callback URLs now include https://<DevHost>/… (stack output DevHost) — spec §5
```

Shell on the box: `aws ssm start-session --target <InstanceId> --profile stockalert-admin`.
Operator tooling that Caddy blocks (`/docs`, `/mcp`): port-forward with
`AWS-StartPortForwardingSession` to the `api` container's published port
(none by default — `docker compose exec api …` or add a `127.0.0.1:8000:8000`
mapping locally on the box).

## Verifying a box

- `systemctl status stockalert-env stockalert` — both `active (exited)`.
- `docker compose ps` in `/opt/stockalert` — five services `running`, api/worker `healthy`.
- `https://<DEV_HOST>/health` → `{"status":"ok","clickhouse":true}`; `/docs` → 403.
- CloudWatch Logs `/stockalert/dev`, streams named by container.
- `grep "render_env:" /var/log/messages` (or `journalctl -u stockalert-env`) — the key count and host.

## Tested locally (this PR) / not tested

Tested on the Mac: `docker buildx build --platform linux/arm64` of the image;
the image serving `/health` and `/app/` with `AUTH_ENABLED=false` against a
throwaway ClickHouse; `docker compose config` of this file with a dummy `.env`
(including the loud failure on a missing `APP_IMAGE`); `aws cloudformation
validate-template` on `stack.yaml`; `bash -n` + usage paths of every script.
**Not run:** the stack itself, `bootstrap.sh` on AL2023, SSM rendering,
Caddy/Let's Encrypt, DLM, the Route 53 check — those are the M2 cutover step
and will be exercised then; expect first-run fixes there.
