# scripts/deploy/

Mac-side release tooling for the AWS box (`infra/dev/`). Spec:
[`docs/operations/deployment_dev.md`](../../docs/operations/deployment_dev.md) §6.
Every script is `set -euo pipefail`, logs every outcome (including the box's
stdout/stderr for remote steps), prints usage on bad arguments, and verifies
what it changed from the other side (ECR has the tag, SSM reads back, `/health`
answers). Nothing here deletes anything.

| Script | Does | Verifies |
|---|---|---|
| `build_push.sh <env> [--allow-dirty]` | `docker buildx build --platform linux/arm64` of the repo `Dockerfile`, push to ECR as `:<sha>` and `:<env>` | `aws ecr describe-images` finds the tag; prints digest + size |
| `release.sh <env> <sha>` | `aws s3 sync infra/<env>/ → s3://<ops>/infra/`, then over SSM on the box: wait for bootstrap, sync down, `bin/release.sh <image>` (pull → `alembic upgrade head` → `up -d`) | box `/health` in-container and via Caddy; then `https://<DevHost>/health` from the Mac |
| `put_param.sh <env> <name>` | stdin → SSM `SecureString` `/stockalert/<env>/<name>` (one trailing newline stripped) | reads it back, compares length; never prints the value |
| `push_schwab_token.sh <env> [--no-auth]` | the 7-day chore: run `scripts/schwab_get_refresh_token.py`, push `data/.schwab_refresh_token`, re-render `.env` on the box, recreate `worker` + `api` | token age < 7 d, SSM readback, container states + `/health` |
| `lib.sh` | shared: profile/region defaults (`stockalert-admin`, `us-east-1`), stack-output lookup, `ssm_run` (send-command, poll, print output, fail on non-Success) | — |

Conventions: `AWS_PROFILE` defaults to `stockalert-admin` and `AWS_REGION` to
`us-east-1`; override in the environment. `<env>` is the CloudFormation stack
suffix (`stockalert-<env>`); stack outputs (`InstanceId`, `OpsBucketName`,
`EcrRepositoryUri`, `DevHost`) are the only place names come from — nothing is
hard-coded on the Mac side.

Typical day:

```bash
scripts/deploy/build_push.sh dev          # prints the sha
scripts/deploy/release.sh dev <sha>
```

Weekly: `scripts/deploy/push_schwab_token.sh dev`. While the box streams, the
laptop runs with `STREAM_PROVIDER=none` (one Schwab streamer session).

Not here on purpose: CodeBuild backfills ([`../codebuild/`](../codebuild/README.md)),
the Cognito stack ([`../../infra/auth/`](../../infra/auth/README.md)), and the
one-time ClickHouse volume move (spec §8 M3 — a runbook step, not a script yet).
