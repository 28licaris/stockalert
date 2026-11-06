# AWS deployment — development environment (spec)

Status: **SPEC — approved 2026-11-05** (decisions in §13). Nothing in this
document is built yet. Per [`../standards/engagement.md`](../standards/engagement.md)
the three code changes in §12 are approved as separate PRs; anything beyond
them needs its own sign-off.

Scope: the **dev** environment only. Prod is a separate environment with the
same shape; §11 lists only what changes. Every resource name, SSM path,
hostname and Cognito pool below is parameterised by `ENV=dev` so prod is a
second instantiation, not a fork.

## 0. The ask, restated

1. One shared StockAlert instance on AWS, over HTTPS, that friends can sign
   up to and use **as customers**, while the operator uses the admin surfaces.
2. Login, customer pages and admin pages behave exactly as the access model
   in [`../product/access_control.md`](../product/access_control.md) says.
3. Nightly ingest, backfills and the paper-trading pipeline **run on the
   server** and **do not degrade customer requests**.
4. Cheap. Dev and prod will be separate; build dev now.

## 1. Decisions

| Decision | Choice | Why — and what was rejected |
|---|---|---|
| Compute | **One Graviton EC2 instance** (`t4g.large`, 2 vCPU / 8 GiB, arm64) running Docker Compose | ClickHouse needs a local disk and ~2–3 GiB RAM regardless, so a "serverless" layout still ends up with an EC2 box. ECS Fargate + RDS + ALB costs ~2.5× (§9) and adds three moving parts for zero dev benefit. Apple Silicon builds arm64 images natively, so no cross-compile. |
| Process topology | **Two app containers from one image**: `api` (request path) and `worker` (live streamer + every scheduled loop). Plus `clickhouse`, `postgres`, `caddy`. | This is what makes requirement 3 true: jobs cannot block the API event loop or starve its GIL if they are not in its process. The flags to do this already exist — every writer is env-gated and `STREAM_PROVIDER=none` disables the streamer (sentinel added in C3 — see the note in §3). See §4. |
| Ingress + TLS | **Caddy** reverse proxy with automatic Let's Encrypt certificates. No ALB. | ALB is ~$17/month before traffic and buys nothing at this scale. Cognito requires an `https://` callback for anything but localhost, so TLS is mandatory, not optional. |
| DNS | **`sslip.io` for now**: `DEV_HOST=app.<eip-with-dashes>.sslip.io`, no Route 53 record | No domain yet (§13). sslip.io resolves to the IP embedded in the name; Let's Encrypt and Cognito both accept it. When a domain exists, `DEV_HOST` changes, the Cognito callback list and the Stripe webhook URL get one more entry, and nothing else moves. `dev.<domain>` below means `DEV_HOST`. |
| Identity | The **existing dev Cognito pool** (`stockalert-auth-dev` stack, `us-east-1_NxTarvrk4`), self-signup on, email verification via Cognito's default sender | Already deployed and verified (AUTH-4). Only its callback/logout URLs change. Friends = customers; the operator is `ADMIN_EMAILS`. |
| Billing | Stripe **test mode** keys + a test-mode webhook endpoint | Friends can "subscribe" with `4242…` cards and exercise the full checkout → webhook → entitlement path at zero cost. |
| Secrets | **SSM Parameter Store** `SecureString` under `/stockalert/dev/*`, rendered into an env file at boot by a systemd unit | Free. Secrets Manager is $0.40/secret/month × ~15 secrets for no gain in dev. |
| Box access | **SSM Session Manager**; no inbound SSH, no key pairs | Free, IAM-audited, and the security group never opens port 22. |
| Images | Built on the Mac with `docker buildx --platform linux/arm64`, pushed to **ECR** | The t4g box has 2 vCPUs; building the frontend there is slow and competes with the market-hours streamer. ECR is ~$0.10/month. |
| Data move | SUPERSEDED 2026-11-05 by `scripts/ch_bootstrap.py` (state snapshot + lake rebuild, nothing uploaded from the laptop except a few MB). Originally: ClickHouse: **tar the Docker volume** from the laptop → S3 → restore. Identity PostgreSQL: **fresh** + `alembic upgrade head`. | ClickHouse holds state that is *not* rebuildable from the lake (`stream_universe`, `strategy_definitions`, `paper_state`, watchlists, `ingestion_runs`, options `*_latest`). A volume copy preserves all of it exactly; a lake rebuild would not. Identity rows are dev accounts; friends sign up fresh and the operator's row is recreated on first login. |
| Backups | Daily EBS snapshot (Data Lifecycle Manager, 7-day retention) + nightly `pg_dump` to S3 | Covers the non-rebuildable ClickHouse state above and the identity DB. |
| Logs + alerting | Docker `awslogs` driver → CloudWatch Logs (14-day retention); Route 53 health check on `/health` → SNS email | Minimal and free-ish. The "no bars during market hours" alarm the runbook asks for is a follow-up (§12). |
| Heavy backfills | **CodeBuild, unchanged** (`scripts/codebuild/`) | Already the design; nothing about this deploy changes it. |
| Paper daily pipeline | **cron on the box**, `docker compose run --rm worker python scripts/paper_daily_pipeline.py` | Resolves the hard-coded laptop path in `docs/TODO.md`. `ewt-email-scan` needs the operator's inbox and **stays on the laptop**. |
| Infra-as-code | **CloudFormation** (`infra/dev/stack.yaml`) | Repo rule: no CDK / Terraform. The Cognito pool is already CFN (`infra/auth/cognito.yaml`). |

## 2. Topology

```
                         ┌────────────────────────── EC2 t4g.large (arm64), us-east-1 ──────────────────────────┐
  friends / operator     │                                                                                       │
  ───── https 443 ─────► │  caddy ──┬── default ───────────────────────► api     (uvicorn :8000)  ──┐           │
                         │          │                                     no streamer, no loops     │  CH http   │
  Cognito hosted login   │          └── /api/v1/jobs*, /api/v1/stream*,                             ├─► clickhouse (:8123, 3 GiB cap)
  (us-east-1_NxTarvrk4)  │              /api/v1/backfill*, /api/v1/health/services ► worker  (uvicorn :8000) ─┘           │
                         │                                                     streamer + every loop   │           │
  Stripe test webhook ─► │              /mcp, /docs, /openapi.json ─► 403      cpus: 1.0               ├─► postgres (:5432, identity)
                         │                                                                             │           │
                         │  cron 23:30 UTC ── docker compose run worker paper_daily_pipeline.py ───────┘           │
                         │  systemd: render-env (SSM → .env) → docker compose up                                   │
                         └─────────────────┬───────────────────────────────────────┬───────────────────────────────┘
                                           │ instance role                         │ EBS gp3 100 GiB (DLM snapshots)
                                           ▼                                       
                         S3 lake + Glue + Athena · SSM params · ECR · CloudWatch Logs · Cognito user-admin APIs
```

Both `api` and `worker` run the **same image and the same identity config**
(same `IDENTITY_DATABASE_URL`, same Cognito settings). That is what lets Caddy
route the operator control-plane paths straight to the worker: the session
cookie is same-origin, so `require_operator` on the worker validates it
identically. No proxy code in the app.

## 3. Containers and their flags

| Container | Image | Role | Key env (only the differences) | Limits |
|---|---|---|---|---|
| `caddy` | `caddy:2` | TLS termination, path routing, `/mcp` + `/docs` block | `DEV_HOST` | — |
| `api` | `stockalert/app:<sha>` | Every customer + auth + billing request | `STREAM_PROVIDER=none` (see note) · all `*_NIGHTLY_ENABLED=false` · `OPTIONS_SNAPSHOT_ENABLED=false` · `CH_RECONCILE_ENABLED=false` · `NEWS_INGEST_ENABLED=false` · `ELLIOTT_RECOMPUTE_ENABLED=false` · `ELLIOTT_LIVE_SCANNER_ENABLED=false` · `JOURNAL_ENABLED=false` · `LIVE_LAKE_WRITER_ENABLED=false` · `SYMBOL_HOTLOAD_ENABLED=true` · `SYMBOL_GAPFILL_ENABLED=true` | none (it is the thing we protect) |
| `worker` | same | Schwab streamer, batcher, all scheduled loops, options snapshots, journal sync | the inverse: `STREAM_PROVIDER=schwab` and every loop flag at its `.env.example` default; `OPTIONS_SNAPSHOT_ENABLED=true` **here and nowhere else** | `cpus: "1.0"`, `mem_limit: 2500m` |
| `clickhouse` | `clickhouse/clickhouse-server:24.8` | hot tier | config.d: `system_logs.xml` (7-day TTL on `query_log`, `part_log`; `trace_log`/`query_thread_log` off — the laptop's `system` DB is **13 GiB**, bigger than `stocks` at 5.5 GiB) | `mem_limit: 3g` |
| `postgres` | `postgres:16-alpine` | identity | password from SSM | — |

**`STREAM_PROVIDER=""` does not disable the streamer** (found while building
C2): `Settings.effective_stream_provider` falls back to `DATA_PROVIDER`, so an
empty value still initialises Schwab. `.claude/launch.json` relies on the same
wrong assumption. C3 adds a `none` sentinel in `app/config.py`
(`effective_stream_provider` returns `""` for it) and the `api` container sets
`STREAM_PROVIDER=none`; `launch.json` gets the same value.

The `worker` env is written as two compose YAML anchors, `stream-flags`
(streamer, batcher, journal sync, options snapshots) and `jobs-flags` (every
nightly loop), merged into one service. Splitting them into `stream` + `jobs`
containers later is a second service definition, not a code change (§4a).

`uvicorn` in both app containers runs with `--proxy-headers
--forwarded-allow-ips='*'` (Caddy is the only thing that can reach them) so
`request.url` reports `https` behind the proxy. `AUTH_COOKIE_SECURE=true`.

`AWS_PROFILE` is **unset** in every container — credentials come from the
instance role (`config.py`'s `_normalize_aws_env` already handles the blank
case). `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` is set globally.

### Caddy routing

| Path | Upstream | Why |
|---|---|---|
| `/api/v1/jobs*` | `worker:8000` | `JobRegistry` is in-process; the Health page's job list + ▶ button must hit the process that owns the loops |
| `/api/v1/stream*` | `worker:8000` | `stream_service.status()` / add / remove act on the live subscription set |
| `/api/v1/backfill*` | `worker:8000` | `backfill_service` queue |
| `/api/v1/health/services` | `worker:8000` | reports streamer / backfill / monitors state of the worker (the API container has none) |
| `/mcp*`, `/docs`, `/redoc`, `/openapi.json` | **403** | MCP has no cookie login; the OpenAPI UI is operator tooling. Reach them via `aws ssm start-session … AWS-StartPortForwardingSession` to `api:8000`. |
| everything else | `api:8000` | |

### In-process coupling that the split has to respect

| Route / feature | Touches | In the split |
|---|---|---|
| Customer watchlist add (`/api/v1/my/watchlists`) | `stream_service.ensure_streaming()` writes the `stream_universe` row **and subscribes in-process** | In `api` the row is written and the subscribe is a logged no-op (no provider). The worker only reads the universe at `start()`. **→ code change C2** (§12): the worker re-syncs its subscription set from `stream_universe` every 60 s using the existing `_read_universe` + `_apply_subscription_diff`. |
| Hotload-on-add / read-path gap-fill | Schwab REST → CH, request-driven | Stays in `api`: it is latency the customer asked for. Shares the Schwab token and rate limit with the worker; acceptable. |
| `/ws/signals`, `monitor_manager` | live detector output | Monitors page is flag-off; lives in `worker`; `/ws` is not routed in dev. |
| `/health` (public liveness) | CH ping | `api`; this is what the Route 53 check hits |
| `/api/v1/health/freshness` | CH tables | `api` |

## 4. Why requirement 3 holds

- **Separate process.** The GIL and the asyncio event loop are per process.
  A `to_thread` PyIceberg append or a Polars union in the worker cannot stall
  a customer's `/api/v1/bars` in the API container.
- **CPU quota.** `worker` is capped at one of the two vCPUs; the API container
  is not capped. ClickHouse is memory-capped so a bad query cannot OOM the box.
- **Schedules are mostly off-hours anyway.** The table below is the honest
  exposure map; only the options snapshot loop runs during the session, and it
  is I/O-bound (Schwab REST + a small Iceberg append).

| Loop (worker) | UTC | ET | During the session? |
|---|---|---|---|
| `backfill_gap_sweeper` | 06:00 | 02:00 | no |
| `nightly_equities_polygon_refresh` (grouped-daily) | 07:00 | 03:00 | no |
| `futures_polygon_nightly` | 21:00 | 17:00 | no |
| `nightly_schwab_refresh`, `nightly_futures_refresh`, `ch_reconcile` | 22:00 | 18:00 | no |
| paper daily pipeline (cron) | 23:30 | 19:30 | no (~30 min; confirm the laptop's current time before moving it) |
| `options_snapshot_refresh` | every 5–15 min | — | **yes** — I/O-bound, one writer only |
| `journal_sync`, `news_ingest` | every 5 / N min | — | yes, trivial |

- **Proof, not assertion.** Acceptance test §10.10 measures API p95 during a
  forced nightly run before we call this done.

**Schwab allows one streamer session.** The moment the worker streams, the
laptop must run with `STREAM_PROVIDER=none` (the sentinel C3 adds).
This is a cutover step (§8), not a nice-to-have.

### 4a. Job placement — why not Lambda, and the roadmap

Decided 2026-11-05: nightly jobs stay on the box, in the `worker` process.
Lambda is a poor fit for them:

- **15-minute cap.** The paper pipeline (~30 min), the paced Polygon
  grouped-daily refresh and the futures continuous rebuild (1–2 min per root)
  all exceed it; chopping them into checkpointed invocations is new code.
- **ClickHouse coupling.** Gap sweeper, `ch_reconcile`, journal sync and the
  paper pipeline read or write CH, which lives on the box. A Lambda in the VPC
  then needs a NAT gateway (~$32/month) or interface endpoints to reach S3,
  Glue and the providers — more than every job's compute combined.
- **Zero marginal cost.** The box runs 24/7 for the streamer and CH anyway.
- **Observability is in-process.** `JobRegistry`, the health page's ▶ button
  and `ingestion_runs` assume a loop that can reach CH.
- **Single-writer Iceberg rule** is simplest to enforce in one process.

Roadmap — each step is pulled only when the previous one bites:

| Step | When | What | Code? |
|---|---|---|---|
| 1 | now | `api` / `worker` | flags (this spec) |
| 2 | a nightly OOM takes the streamer down, or the worker is crowded | `worker` → `stream` + `jobs` containers | flags only (the anchors in §3) |
| 3 | a lake-only job outgrows the box | lift it to an EventBridge-scheduled Fargate task or CodeBuild run from the same image. Lake-only jobs need no CH and no VPC plumbing. Futures rebuild first (`docs/TODO.md` already targets CodeBuild). CH-touching jobs stay on the box | one-shot CLI entry point per job (below) |
| 4 | never for the nightlies | Lambda for micro-utilities only: Schwab token-expiry probe, "no bars during market hours" alarm, backup trigger | small |

Groundwork that makes step 3 a scheduler change instead of a rewrite: every
nightly gets a one-shot entry point (`python -m app.services.ingest.run
<job> --date YYYY-MM-DD`) that records through the same `audit_run` path the
loop uses. Optional addition to C2's scope (§12).

## 5. Auth, customer and admin surfaces — what has to be true

### Cognito (existing stack, one parameter change)

```bash
aws cloudformation deploy --stack-name stockalert-auth-dev \
  --template-file infra/auth/cognito.yaml \
  --parameter-overrides EnvironmentName=dev DomainPrefix=<existing prefix> \
    CallbackUrls=https://dev.<domain>/auth/callback,http://localhost:8000/auth/callback \
    LogoutUrls=https://dev.<domain>/app/login,http://localhost:8000/app/login
```

Keeping the localhost URLs means `scripts/dev/run_auth_api.sh` still works
against the same pool. Self-signup stays on (`AllowAdminCreateUserOnly:
false`), MFA stays optional, password policy stays 12-char. Cognito's default
email sender is capped at ~50 messages/day — enough for a handful of friends;
prod moves to SES.

### App settings (dev)

| Setting | Value |
|---|---|
| `AUTH_ENABLED` | `true` |
| `ADMIN_EMAILS` | the operator's email (§13 Q5) |
| `COGNITO_DOMAIN` / `COGNITO_ISSUER_URL` / `COGNITO_CLIENT_ID` | from the stack outputs (already in `run_auth_api.sh`) |
| `COGNITO_CLIENT_SECRET` | SSM |
| `COGNITO_REDIRECT_URI` | `https://dev.<domain>/auth/callback` |
| `COGNITO_LOGOUT_URI` | `https://dev.<domain>/app/login` |
| `AUTH_COOKIE_SECURE` | `true` |
| `AUTH_PROVIDER_TOKEN_CIPHER` | `local` (AES-GCM keyed from the client secret). `kms` is prod. |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_PRICE_*` | **test-mode** values, SSM |
| `BILLING_RETURN_URL` | `https://dev.<domain>/app/settings` |
| Stripe webhook endpoint (configured in the Stripe dashboard) | `https://dev.<domain>/api/v1/customer/billing/webhook` (the billing router is mounted at `/customer/billing`) |

### The gap that blocks sharing the URL — code change C1

Today only six route files carry an auth dependency (`routes_auth`,
`routes_customer_auth`, `routes_admin_auth`, `routes_billing`,
`routes_my_watchlists`, and the operator-gated routers). **Every market-data,
research, options, backtest and strategy endpoint is open to anyone who has
the hostname**, with or without a session — the SPA's `AuthGuard` is UX, not
security, as `access_control.md` says itself. On a laptop behind Tailscale
that was fine. On a public hostname it is not.

C1 adds one deny-by-default ASGI middleware, active only when
`AUTH_ENABLED=true`: any request to `/api/`, `/cockpit/`, `/ws/` or `/mcp`
without a valid session gets `401 {"error":"authentication_required"}`.
Allowlist: `/health`, `/auth/*`, `/app/*` (static SPA), `/api/v1/customer/billing/webhook`
(Stripe signature is its auth). Built as PR #43. Operator routers keep `require_operator` on
top. **Follow-up (role policy):** the gate is also default-deny for non-operators,
with an explicit customer-allowed route table; see
[`access_control.md`](../product/access_control.md). Subscription-entitlement
gating of premium pages stays a TODO.

### Expected behaviour matrix (this is acceptance test §10.3–10.4)

| Who | `/app/charts` | Sidebar | `/app/` (System Health), `/app/stream`, `/app/clickhouse` | `GET /api/v1/jobs` | `GET /api/v1/bars` |
|---|---|---|---|---|---|
| Logged out | redirect → Cognito login | — | redirect | 401 | 401 (after C1) |
| Friend (customer) | ✅ | no admin items, no StatusBar | redirect to `/charts` | 403 | ✅ |
| Operator (`ADMIN_EMAILS`) | ✅ | admin items + StatusBar | ✅ with live data from the worker | 200 | ✅ |

## 6. Build and image — code change C3

The current `Dockerfile` cannot run this deployment:

- it `COPY`s `.env.example` to `.env`, so any variable not set in the
  container env silently takes the example's value (`COGNITO_DOMAIN=https://your-domain…`);
- it does not build the frontend, so `/app/` 404s;
- it omits `scripts/`, `migrations/`, `alembic.ini`, `configs/` and
  `data/market_events_seed.json`, so `alembic upgrade head`, the paper pipeline
  and the market calendar all fail.

C3 is a two-stage Dockerfile: `node:20` builds `frontend/` into
`app/static/dist`; `python:3.12-slim` installs the project and copies `app/`,
`scripts/`, `migrations/`, `alembic.ini`, `configs/`, `data/*.json`. No `.env`
in the image. Non-root user. `HEALTHCHECK` on `/health`. A `.dockerignore`
keeps `data/`, `ewt/`, `docs/`, `.git` out of the context. Tag = git SHA, plus
a moving `dev` tag.

Release flow (Mac side, `scripts/deploy/`):

| Script | Does |
|---|---|
| `build_push.sh <env>` | `docker buildx build --platform linux/arm64`, push to ECR `stockalert/app:<sha>` and `:<env>` |
| `release.sh <env> <sha>` | `aws ssm send-command` → on the box: `docker compose pull`, `docker compose run --rm api alembic upgrade head`, `docker compose up -d`, then `curl /health`. Migrations run **before** the new API starts, never at startup. |
| `put_param.sh <env> <name>` | reads a value from stdin into `/stockalert/<env>/<name>` (SecureString) |
| `push_schwab_token.sh <env>` | the 7-day chore: runs `scripts/schwab_get_refresh_token.py` locally, pushes `data/.schwab_refresh_token` to SSM, restarts `worker` and `api` |

## 7. AWS resources (`infra/dev/stack.yaml`)

| Resource | Spec |
|---|---|
| VPC | default VPC, one public subnet |
| Security group | inbound 80 + 443 from `0.0.0.0/0` (80 only for the ACME challenge, Caddy redirects); **no 22**; outbound all |
| IAM role + instance profile | `AmazonSSMManagedInstanceCore`; S3 RW on `STOCK_LAKE_BUCKET` + the backup bucket; Glue read/write on the `equities`, `futures`, `options` databases; Athena start/get on its results bucket; `ssm:GetParametersByPath` on `/stockalert/dev/*` + `kms:Decrypt` on the SSM key; ECR pull; `logs:*` on the log group; `cognito-idp:GetUser/AssociateSoftwareToken/VerifySoftwareToken/SetUserMFAPreference` on the pool (TOTP enrolment, per `identity/README.md`) |
| EC2 | `t4g.large`, Amazon Linux 2023 arm64, IMDSv2 required, 100 GiB gp3 root, 2 GiB swapfile, user-data installs Docker + Compose plugin and the systemd units |
| Elastic IP | attached; `DEV_HOST` derives from it (sslip.io). No Route 53 zone until a domain exists |
| DLM policy | daily snapshot of the root volume at 09:00 UTC, keep 7 |
| S3 bucket | `stockalert-dev-ops`: `pg_dump/` (30-day lifecycle), `clickhouse-migrate/` (the one-time volume tar) |
| ECR | `stockalert/app`, lifecycle: keep last 10 |
| CloudWatch | log group `/stockalert/dev`, 14-day retention; alarm on the health check; disk-used alarm via the CloudWatch agent |
| SNS | topic `stockalert-dev-alerts` → operator email |
| Route 53 health check | HTTPS `DEV_HOST/health`, 30 s. Needs no hosted zone — it checks an endpoint, not a record |

The box layout: `/opt/stockalert/{docker-compose.yml, Caddyfile,
clickhouse/config.d/, .env (0600, rendered), bin/release, bin/pg_backup}`.
systemd: `stockalert-env.service` (oneshot: SSM → `.env`) → `stockalert.service`
(`docker compose up -d`, `Restart=always`). cron: `23:30 UTC` paper pipeline,
`04:00 UTC` `pg_backup` → S3.

SSM parameters (`/stockalert/dev/…`): `schwab_client_id`, `schwab_client_secret`,
`schwab_refresh_token`, `polygon_api_key`, `cognito_client_secret`,
`postgres_password`, `clickhouse_password`, `stripe_secret_key`,
`stripe_webhook_secret`, `anthropic_api_key`, `bls_api_key`, `bea_api_key`.
Non-secret config lives in the committed `infra/dev/app.env` (bucket name,
region, hostname, flags per §3).

Sizing note: `t4g.large` is tight on memory (CH 3 GiB + worker 2.5 GiB + API
~0.8 GiB + PG + Caddy ≈ 7 GiB). If the worker OOMs on a nightly, the fix is
a stop → change instance type → start to `t4g.xlarge` (16 GiB, +$49/month).
Nothing else changes.

## 8. Cutover plan

| Step | What | Done when |
|---|---|---|
| M0 | §13 decided; Stripe test keys + webhook created (webhook URL known only after the EIP exists, so it is created in M2) | — |
| M1 | C1 + C2 + C3 land as three separate PRs with tests | `poetry run pytest -m "not integration"` green; image runs locally with `AUTH_ENABLED=true` via the compose file |
| M2 | `infra/dev/stack.yaml` deployed; SSM params loaded; Cognito stack re-deployed with the new URLs | `https://dev.<domain>/health` → `{"status":"ok"}`; operator can log in; `api` + fresh CH/PG only — worker not yet streaming |
| M3 | Data move, scripted from S3 (no volume copy): on the laptop run `scripts/lake_archive_ch.py --family all --apply` then `scripts/ch_state_snapshot.py export`; stop the laptop's streamer and writers (`STREAM_PROVIDER=none`); on the box remove the M2 override, run `scripts/ch_bootstrap.py --state-mode replace`, then start `worker` | `ch_bootstrap` verify passes; Health page shows `streaming_count > 0`; `ingestion_runs` grows that night |
| M4 | Point the paper pipeline cron at the box; disable the laptop scheduled task; wire the health check → SNS | the next morning's `ingestion_runs` has every nightly + the paper run; §10.10 latency test passes |
| M5 | Invite friends | each one signs up, lands on `/charts`, sees no admin items |

Rollback at any step is "start the laptop stack again" — the lake is the
source of truth and both machines read it. Only one may **stream** at a time.

## 9. Cost (us-east-1, on-demand, monthly, approximate)

| Item | Dev |
|---|---|
| EC2 `t4g.large` | ~$49 |
| EBS gp3 100 GiB | ~$8 |
| Elastic IP | ~$3.65 |
| Route 53 health check (no zone) | ~$0.50 |
| EBS snapshots (7-day, changed blocks) | ~$2–4 |
| CloudWatch Logs | ~$1–2 |
| ECR, S3 ops bucket, SNS, SSM, Session Manager, Cognito (≤10k MAU), Stripe test | ~$0 |
| **New spend** | **≈ $66–70** |
| Already paid (lake S3 + Glue + Athena + CodeBuild) | $7–10 + usage |
| If `t4g.xlarge` is needed | ≈ $115 |

Once the size is stable for a month, a 1-year no-upfront Compute Savings Plan
takes ~30% off the EC2 line. Not before.

Rejected for comparison: Fargate `api` + `worker` (~$36 each at 1 vCPU/2 GiB)
+ RDS `db.t4g.micro` (~$12) + ALB (~$17) + an EC2 for ClickHouse anyway ≈
$150+/month, with four more services to operate.

## 10. Acceptance tests

1. `curl https://dev.<domain>/health` → `{"status":"ok","clickhouse":true}`; port 80 redirects to 443; certificate valid.
2. `curl https://dev.<domain>/api/v1/bars?symbol=SPY` **without a cookie** → 401. `/mcp`, `/docs` → 403.
3. Fresh browser: `/app` → Cognito hosted sign-up → verification email arrives → lands on `/app/charts`; sidebar has no System Health / Stream / ClickHouse; StatusBar absent; `/app/clickhouse` redirects to `/charts`; `GET /api/v1/jobs` → 403.
4. Operator login: System Health lists services **and** scheduled jobs with `last_success`; Stream page shows `streaming_count > 0` during the session; ▶ on `backfill_gap_sweeper` → `started`, then a row in `ingestion_runs`.
5. Customer adds a symbol not in the universe to a watchlist → within 60 s the worker log shows the subscribe → 1-minute bars appear on the chart (C2).
6. Settings: TOTP enrolment completes (QR → code → `mfa_enabled` security event); sessions list shows the current session; "revoke others" works from a second browser.
7. Billing: Checkout with `4242 4242 4242 4242` → webhook `200` in the API log → subscription status `active` on Settings; Customer Portal opens.
8. Logout clears the cookie and the Cognito session (a second `/app` visit prompts for login).
9. Next morning: `ingestion_runs` has `nightly_schwab_refresh`, `nightly_futures_refresh`, `nightly_equities_polygon_refresh`, `ch_reconcile`, `backfill_gap_sweeper` and the cron'd paper run, none with `last_status=error`; `/app/health` freshness page green.
10. **Isolation:** 60-second `curl` loop against `/api/v1/bars` (p95 recorded) → trigger `nightly_schwab_refresh` manually → repeat the loop during the run. p95 moves by less than 20%. Record both numbers in the PR.
11. `sudo reboot` → every container is back within 2 minutes with no operator action; streamer reconnects.
12. A DLM snapshot and a `pg_dump/` object exist; a restore of `pg_dump` into `postgres-test` succeeds.
13. Stop the laptop stack entirely for a trading day: nothing in §9 regresses.

## 11. What changes for prod (not built now)

RDS PostgreSQL Multi-AZ instead of the container (per
[`identity_and_subscriptions.md`](../product/identity_and_subscriptions.md) §4.2);
`AUTH_PROVIDER_TOKEN_CIPHER=kms` with a customer-managed key; SES for Cognito
email; a separate Cognito pool `stockalert-auth-prod`; Stripe live keys;
`MfaConfiguration` required for operators; a larger instance or ECS for `api`
with ClickHouse on its own box; the marketing site on S3 + CloudFront at the
apex (`docs/TODO.md`). Same compose file, same image, `ENV=prod`.

## 12. Code changes (each its own PR, each needs sign-off)

| # | Change | Files | Estimate |
|---|---|---|---|
| C1 (PR #43) | Deny-by-default session middleware for `/api/`, `/cockpit/`, `/ws/`, `/mcp` when `AUTH_ENABLED=true`, with the allowlist in §5; tests for 401/allowlist/operator | `app/main_api.py`, `app/api/auth_dependencies.py` (or new `app/api/auth_middleware.py` + README entry), `app/api/tests/` | ~2 h |
| C2 (PR #44) | Worker universe re-sync loop: every 60 s diff `stream_universe` ∪ `futures_universe` against `_subscribed` and apply; registered with `JobRegistry` as `stream_universe_resync` (frequent heartbeat). **Optional:** the one-shot job entry point from §4a | `app/services/stream/service.py`, `app/main_api.py`, `app/services/stream/README.md`, tests (+ `app/services/ingest/run.py` if the option is taken) | ~2 h (+2 h) |
| C3 | `STREAM_PROVIDER=none` sentinel in `app/config.py` (+ `.env.example`, `.claude/launch.json`); two-stage Dockerfile + `.dockerignore` + `infra/dev/{docker-compose.yml, Caddyfile, clickhouse/config.d/system_logs.xml, app.env, systemd/*, bootstrap.sh, stack.yaml}` + `scripts/deploy/*.sh` + `infra/README.md` | new files; `Dockerfile` | ~1 day incl. first deploy |

Follow-ups filed, not in scope: "no bars in `ohlcv_1m` during market hours"
CloudWatch metric + alarm; subscription-entitlement gating of premium pages;
the delivered-vs-subscribed health comparison (`docs/TODO.md`); SES.

## 13. Decisions (2026-11-05)

| Question | Decision |
|---|---|
| Hostname | `sslip.io` (`app.<eip-with-dashes>.sslip.io`); no domain yet. Rename when one exists. |
| AWS account / region | One account via the `stockalert-admin` profile, `us-east-1`. |
| Instance | `t4g.large`; step up to `t4g.xlarge` only if the worker OOMs. |
| Google sign-in | Off in dev. Email + password only. |
| Operators | `ADMIN_EMAILS` = the founder's email only. Friends are customers. |
| Topology | `api` + `worker` from day one. |
| Code changes | C1, C2, C3 approved as three separate PRs. The one-shot job entry point is **not** in C2; it stays a §4a follow-up. |

Consequence: no Route 53 hosted zone or domain in §7 and §9. The Route 53
health check stays — it targets an endpoint and needs no zone.
