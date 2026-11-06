# Devbox: develop in AWS from VS Code

A personal cloud dev machine next to the lake. Use it when you want fast lake
reads/transfers or to exercise a workflow in AWS; the laptop stays a full dev
environment too. Built pieces: [`infra/devbox/`](../../infra/devbox/README.md),
[`scripts/devbox/devbox.sh`](../../scripts/devbox/devbox.sh).

**It is never an owner.** The dev box ([`dev_environment.md`](dev_environment.md))
alone streams Schwab and runs the nightlies. The devbox cannot write the lake
(IAM) and its `.env` turns every stream/writer/nightly off.

| | |
|---|---|
| Stack | `stockalert-devbox` (CloudFormation, `infra/devbox/stack.yaml`) |
| Instance | `t4g.xlarge` (4 vCPU, 16 GiB), Amazon Linux 2023 arm64, 100 GiB gp3 — `devbox.sh status` prints the id |
| Login | VS Code Remote-SSH → `stockalert-devbox`, tunnelled through SSM; no inbound ports, no stored key pair |
| User / repo | `ec2-user`, `~/stockalert` (own clone; push/pull with your forwarded GitHub key) |
| Lake | Read-only. Only `s3://<lake>/athena-results/` is writable (Athena needs it) |
| Cost | ~$0.13/h running + ~$8/month disk. ~$30–35/month at 8 h/weekday |

## Measured (2026-11-06)

`read_arrow` of 2025 minute bars, same rows both sides:

| Read | Laptop | Devbox |
|---|---|---|
| 1 symbol (184k rows) | 58 s | **5 s** |
| 8 symbols (1.5M rows, 165 MB) | 232 s | **11 s** |

`pytest -m "not integration"`: 1981 passed in 3.5 min.

## One-time setup (laptop, repo root)

Needs `aws` (profile `stockalert-admin`) and `brew install --cask session-manager-plugin`.

```bash
scripts/devbox/devbox.sh deploy        # the stack (already done 2026-11-06)
scripts/devbox/devbox.sh provision     # tools + auto-stop on the box; re-run after editing infra/devbox/
scripts/devbox/devbox.sh ssh-config    # ~/.ssh/config Host stockalert-devbox + auto-starting proxy
ssh-add --apple-use-keychain ~/.ssh/id_rsa   # GitHub key into the agent (forwarded to the box)
scripts/devbox/devbox.sh clone         # git clone + pin the Poetry venv to Python 3.12
scripts/devbox/devbox.sh push-env      # laptop .env minus AWS creds + devbox overrides + Schwab token
ssh stockalert-devbox 'cd ~/stockalert && poetry install && docker compose --profile ch up -d && docker compose --profile identity up -d postgres'
```

VS Code: install **Remote - SSH**, set `"remote.SSH.connectTimeout": 180` in user
settings (a stopped box takes ~1–2 min to start), then **Remote-SSH: Connect to
Host… → stockalert-devbox** and open `/home/ec2-user/stockalert`. Pick the
interpreter `.venv/bin/python`; `.vscode/launch.json` works unchanged. Ports
(8000, the Vite dev server) forward to your laptop automatically.

## Daily use

Just connect from VS Code — **the proxy starts a stopped devbox for you.**

```bash
scripts/devbox/devbox.sh status          # state, SSM, backstop alarm, last idle decision
scripts/devbox/devbox.sh stop            # done for the day (or let auto-stop do it)
scripts/devbox/devbox.sh keep-awake 4    # long job in tmux after you disconnect (1-24 h)
scripts/devbox/devbox.sh shell           # SSM shell without ssh
```

ClickHouse on the devbox is a **local, empty** copy (nothing streams into it).
Load what you need from the lake — fast here: `poetry run python scripts/rebuild_ch_from_lake.py …`.

## Auto-stop

The box stops itself; a stopped box costs only its disk.

| Rule | Detail |
|---|---|
| Idle | No ssh/VS Code connection and no SSM session for **45 min** → stop. A fresh boot gets a full 45 min |
| Nightly cap | At **08:00 UTC** (03:00 ET) it stops even if a VS Code window is still open |
| Keep awake | `devbox.sh keep-awake <h>` (or `devbox-keep-awake <h>` on the box) skips both rules until it expires |
| Backstop | CloudWatch alarm `stockalert-devbox-idle-backstop` stops it after 3 h under 2% CPU, in case the timer fails |

A sleeping laptop drops the ssh connection within ~3 min (sshd keepalive), so
the idle clock starts then. Every check logs one line:
`journalctl -u devbox-idle-stop -n 20`. Tune `IDLE_MINUTES` / `NIGHTLY_STOP_HOUR_UTC` in
`/etc/devbox.conf` on the box (`sudo vi`); it takes effect on the next 5-minute
check and `provision` never overwrites it.

## Rules

- **Never an owner.** Don't flip `STREAM_PROVIDER` or any `*_ENABLED` writer on in the devbox `.env`.
  Schwab allows one streamer; the dev box has it.
- **No Schwab re-auth on the devbox.** It reuses the laptop token for REST. Re-auth on the
  laptop, `scripts/deploy/push_schwab_token.sh dev`, then `devbox.sh push-env`.
- **Lake writes fail with AccessDenied — by design.** Backfills and other lake writers run
  on the dev box (or laptop), not here. Widening the role is a spec change.
- **No secrets in the repo.** `push-env` copies them from the laptop `.env` and refuses to
  ship AWS credentials; the devbox uses its instance role.
- **Disk survives stops; `delete-stack` deletes it.** Push your branches.
