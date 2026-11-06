# Dev environment: log in and get things done

The dev server is one AWS box (`t4g.large`, us-east-1) running Docker Compose: `caddy`
(HTTPS), `api`, `worker` (streamer and every nightly job), `clickhouse`, `postgres`.
There is **no SSH**. You reach it with AWS Session Manager. Design: [`deployment_dev.md`](deployment_dev.md).
To **develop** in AWS (VS Code, fast lake reads) use the separate devbox: [`devbox.md`](devbox.md).

| | |
|---|---|
| Site | https://app.54-82-165-71.sslip.io |
| Instance | `i-01baeadb537bf6b1d` (also the `InstanceId` output of stack `stockalert-dev`) |
| AWS | profile `stockalert-admin`, region `us-east-1` |
| Logs | CloudWatch group `/stockalert/dev` (14 days) |

## One-time setup (laptop)

```bash
brew install --cask session-manager-plugin     # Session Manager needs this plugin
export AWS_PROFILE=stockalert-admin AWS_REGION=us-east-1
aws sts get-caller-identity                    # should show account 562741918372
```

## Log in to the host

```bash
aws ssm start-session --target i-01baeadb537bf6b1d
sudo -i
cd /opt/stockalert
```

Everything lives in `/opt/stockalert` (compose file, Caddyfile, `release.env`). Never edit `.env`
there: it is rendered from `infra/dev/app.env` plus SSM secrets on every release.

## Log in to the app

- **Operator:** open the site, sign in with the `ADMIN_EMAILS` address. You see System Health,
  Stream and ClickHouse; customers do not.
- **Friends:** they sign up at the same URL and land on the customer pages.

## Day-to-day on the box (after `sudo -i; cd /opt/stockalert`)

```bash
docker compose ps                                  # all five should be running/healthy
docker compose logs -f --tail 100 worker           # also: api, caddy, clickhouse
docker compose restart worker                      # code/data only; env changes need the next line
docker compose up -d --force-recreate worker
docker compose exec clickhouse clickhouse-client --password "$(grep '^CLICKHOUSE_PASSWORD=' .env | cut -d= -f2- | tr -d "'")"
docker compose exec postgres psql -U stockalert -d stockalert_identity
docker compose run --rm --no-deps worker python scripts/<script>.py     # a one-off job in the app image
df -h / ; free -m                                  # disk and memory
```

Use `docker compose run`, never `docker run --env-file`: the rendered `.env` single-quotes its
values and plain docker keeps the quotes, so passwords fail.

## From the laptop (repo root)

```bash
scripts/deploy/build_push.sh dev                   # builds arm64, pushes to ECR, prints the <sha>
scripts/deploy/release.sh dev <sha>                # syncs config, migrates, restarts, checks /health
printf '%s' 'value' | scripts/deploy/put_param.sh dev <name>   # store a secret (SSM), then release
scripts/deploy/push_schwab_token.sh dev            # Schwab refresh token: every 7 days
```

Details: [`scripts/deploy/README.md`](../../scripts/deploy/README.md). Rebuild or restore the
ClickHouse data: [`runbook.md`](runbook.md) ("Rebuild ClickHouse from the lake").

## Rules that keep it safe

- **One owner.** Only one machine may stream from Schwab or run the nightly jobs. That is the box;
  keep the laptop's API and its `daily-paper-trade-momentum` task off.
- **A temporary file means the worker is held off.** If `/opt/stockalert/docker-compose.override.yml`
  exists, the worker streams and writes nothing. It is removed at the cutover.
- **Secrets stay in SSM** (`/stockalert/dev/*`), never in the repo or chat.

## Where the data is backed up

| What | Where |
|---|---|
| Whole disk | Daily EBS snapshot, 7 kept |
| Identity database | `pg_dump` 04:00 UTC to `s3://stockalert-dev-ops-562741918372/pg_dump/`, 30 days |
| ClickHouse state (not the bars) | `s3://<lake bucket>/ch_state/`, nightly, 14 kept |
| Bars | The Iceberg lake (the source of truth) |

Alerts go to the email subscribed to SNS topic `stockalert-dev-alerts`; confirm its subscription
email once.
