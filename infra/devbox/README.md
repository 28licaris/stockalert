# infra/devbox/

A personal cloud dev machine (VS Code Remote-SSH over SSM) with **read-only**
lake access. How to use it: [`docs/operations/devbox.md`](../../docs/operations/devbox.md).
Laptop-side control: [`scripts/devbox/devbox.sh`](../../scripts/devbox/devbox.sh).

Separate from [`infra/dev/`](../dev/README.md) on purpose: the dev box is the
Schwab/nightly owner and a release target; the devbox is neither and can be
deleted without touching it.

| File | What it does |
|---|---|
| `stack.yaml` | CloudFormation `stockalert-devbox`: SG with **no ingress**, read-only IAM role (S3 get/list on the lake, write only `athena-results/`; Glue reads; Athena; ECR pull; Cognito `DescribeUserPoolClient`), `t4g.xlarge` AL2023 arm64 launch template (IMDSv2, 100 GiB gp3, shutdown = stop), CloudWatch low-CPU backstop alarm that stops the instance |
| `bootstrap.sh` | Run as root by `devbox.sh provision` (shipped over SSM, idempotent). Installs the auto-stop first, then git/tmux/gcc, Python 3.12, Docker + Compose, Node (checksum-verified), Claude Code, `gh`, Poetry, EC2 Instance Connect + sshd drop-in; finishes by asserting the identity is the `stockalert-devbox` role |
| `bin/devbox-idle-stop.sh` | The auto-stop decision (idle 45 min / nightly 08:00 UTC cap / keep-awake). One `decision=` log line per run |
| `bin/devbox-keep-awake` | `devbox-keep-awake <1-24 h> \| off \| status` |
| `systemd/devbox-idle-stop.{service,timer}` | Runs the decision every 5 min |
| `sshd-devbox.conf` | Keys only, no root, 60 s × 3 keepalive so dead connections clear fast |
| `devbox.env` | Appended to the devbox `.env` by `devbox.sh push-env`: `STREAM_PROVIDER=none` and every writer/nightly off |

## Testing

No unit tests (shell + CFN). Verified end to end on 2026-11-06:
`aws cloudformation validate-template`; `provision` → Success; VS Code/ssh connect;
`pytest -m "not integration"` green on the box; S3 put/delete + Glue create/update →
AccessDenied, Athena query → SUCCEEDED; uvicorn with the devbox `.env` → `/health` ok,
no stream, writers and nightlies logged as disabled; idle stop observed with
`IDLE_MINUTES=5`. Dry-run the decision any time: `sudo DRY_RUN=1 devbox-idle-stop.sh`.
