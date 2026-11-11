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

## Release a change to the dev box

Code reaches the box as an arm64 image tagged with the merge commit's 12-character SHA.
Nothing changes on the box until step 4.

1. **CI green and up to date.** A PR needs `backend`, `frontend` and `image` green *and* to be up to
   date with `main`. After any merge, every other open PR is behind: `gh pr update-branch <n>` and wait
   about 3 minutes for CI again. If two open PRs both change `frontend/src/api/types.gen.ts`, the second
   one merges `main`, then regenerates the file (command in the docstring of
   `tests/contract/test_openapi_types_in_sync.py`) and checks it is unchanged.
2. **Merge** with the operator's explicit approval (merge commit, as every PR so far).
3. **Build from the merged commit**, clean tree (the tag must mean what it says):

   ```bash
   git fetch && git checkout --detach origin/main
   scripts/deploy/build_push.sh dev        # prints the <sha>; verifies the tag is in ECR
   ```

4. **Release.** Note the running tag first, it is your rollback target
   (`grep APP_IMAGE /opt/stockalert/release.env` on the box), then release:

   ```bash
   scripts/deploy/release.sh dev <sha>
   ```

   On the box this syncs `infra/dev/`, re-renders `.env` from SSM, pulls, runs
   `alembic upgrade head` in a one-off container **before** anything restarts, then recreates `api`
   and `worker` and checks `/health`. If the migration fails, nothing restarts. Expect the site and the
   Schwab streamer to be down for 1 to 2 minutes, so release in the quiet window, 01:00 to 09:00 UTC
   (no equity session; the equity stream runs 09:00 to 01:00 UTC, regular hours 14:30 to 21:00 UTC).
   It does not touch a running `ch-bootstrap2` rebuild. Caddy and Postgres can recreate if their floating tags moved.
5. **Verify** (about a minute):

   ```bash
   H=https://app.54-82-165-71.sslip.io
   curl -s $H/health                                                    # {"status":"ok","clickhouse":true}
   curl -s -o /dev/null -w '%{http_code}\n' "$H/api/v1/bars?symbol=SPY"  # 401 (no cookie)
   curl -s -o /dev/null -w '%{http_code}\n' $H/mcp                       # 403 (Caddy)
   ```

   On the box (`sudo -i; cd /opt/stockalert`): `docker compose ps` (all healthy, `api` and `worker` on the
   new tag), `docker compose exec -T api alembic current` (the new head), and
   `docker compose logs --since 5m worker | grep "streamer LOGIN success"`. Then sign in and open a page
   you changed. A non-operator test account catches missing customer routes: the route-policy test
   covers the API, not what the pages call.
6. **Roll back** by releasing the previous tag: `scripts/deploy/release.sh dev <previous-sha>`.
   Migrations are not undone; downgrade by hand (`docker compose run --rm --no-deps api alembic downgrade -1`)
   only if the old code cannot run on the new schema.

Other deploy actions: `printf '%s' 'value' | scripts/deploy/put_param.sh dev <name>` stores a secret in
SSM (then release to re-render `.env`), and `scripts/deploy/push_schwab_token.sh dev` renews the Schwab
refresh token every 7 days. Script details: [`scripts/deploy/README.md`](../../scripts/deploy/README.md).
Rebuild or restore the ClickHouse data: [`runbook.md`](runbook.md) ("Rebuild ClickHouse from the lake").
Cognito and other CloudFormation changes are not releases: the operator runs those change sets.

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
