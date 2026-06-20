# AWS Deployment Plan (dev / staging / prod, Terraform + CI/CD)

**Status: DRAFT — needs sign-off before any infra or CI code is written.**
Per [`engagement.md`](standards/engagement.md), this doc is the spec. No
Terraform, Dockerfile, or pipeline code lands until each phase below is
confirmed.

## 1. Goal

Automate provisioning and deployment of three environments (dev,
staging, prod) on AWS with no manual console clicking for routine
deploys. A merge to `main` should be able to reach dev without a human
touching AWS; staging and prod promote the same built artifact behind
an explicit approval gate.

## 2. Scope

**In scope:** VPC/networking, ECS Fargate for the FastAPI backend,
S3 + CloudFront for the SPA in staging/prod (see §4a), self-hosted
ClickHouse on EC2 (per `CLAUDE.md`'s "self-hosted ClickHouse"
decision), RDS Postgres for app-user data, ECR, Secrets Manager,
Terraform modules, GitHub Actions pipeline, Dockerfile changes needed
to build a deployable image, and the small frontend/backend code
changes that splitting origins requires (§4a).

**Out of scope (separate specs, not pre-authorized by this doc):**

- **Postgres application integration.** There is no Postgres usage in
  `app/` today — no SQLAlchemy/Alembic dependency, no service module.
  This plan provisions the *database*; wiring the app to use it (schema
  design, a `app/services/accounts/` module per
  [`service_modules.md`](standards/service_modules.md), Alembic
  migrations) is a separate spec.
- **Custom domain.** No domain is registered yet. Browser-facing HTTPS
  is solved without one (§4a — CloudFront's default certificate covers
  its own `*.cloudfront.net` domain for free). The only residual gap
  is the CloudFront→ALB hop running over plain HTTP internally, and no
  human-friendly URL — both close the moment a domain exists. Not
  blocking for now; revisit if/when a domain is registered.
- **Schwab refresh-token storage.** `SCHWAB_REFRESH_TOKEN_FILE`
  (see [`CONFIG.md`](../CONFIG.md)) assumes a writable local file that
  persists across restarts. Fargate containers are ephemeral — a
  restart loses the refreshed token. Needs a follow-up code change
  (read/write the token via Secrets Manager `PutSecretValue` instead of
  a file). Flagged here, not fixed here.

## 3. Environment isolation model

Two distinct topologies, by design — see §4a for why:

| Resource | dev (lightweight) | staging (mirrors prod) | prod |
|---|---|---|---|
| Frontend | served by FastAPI (`app/static/dist`, monolith image) | S3 + CloudFront | S3 + CloudFront |
| ClickHouse | shared EC2 box, DB `stocks_dev` | same box, DB `stocks_staging` | own EC2 box, DB `stocks` |
| Postgres | shared RDS instance, DB `app_dev` | same instance, DB `app_staging` | own RDS instance, DB `app_prod` |
| Backend (Fargate) | own task/service, API+SPA combined image | own task/service, API-only image | own task/service, API-only image |
| Load balancer | none — task public IP + locked-down SG | ALB (stable CloudFront origin) | ALB (health check `/health`) |
| Always-on? | yes (cheap enough not to bother scheduling stop/start) | yes | yes |

Rationale: ClickHouse/Postgres compute is the fixed cost that doesn't
scale with traffic, so sharing it for the two pre-production
environments is where the savings are. Prod gets dedicated boxes so a
dev/staging mistake (bad query, runaway backfill, disk fill) can never
take prod down. Backend compute is already cheap on Fargate (no idle
EC2 to amortize), so each env gets its own task — the isolation there
is nearly free.

**No NAT Gateway.** A NAT Gateway costs ~$32-35/mo *per environment*
just to exist, before any data transfer — for a low-traffic app this
would be the single largest line item. Fargate tasks run in public
subnets with public IPs but a security group that only allows inbound
from the ALB (prod) or nothing at all (dev/staging — accessed by
task public IP on the app port directly, rotated each deploy so this
is acceptable for non-prod). Outbound internet (PyPI no — image is
prebuilt; Polygon/Schwab/Alpaca API calls — yes) works fine from a
public subnet without NAT. VPC endpoints (S3, ECR, Secrets Manager,
CloudWatch Logs) cost nothing extra in the same region and avoid
sending that traffic over the public internet at all — use those
regardless of NAT decision.

**Confirmed**: public-subnets-with-tight-security-groups over
private-subnets-with-NAT, for cost reasons, with the explicit tradeoff
understood — private subnets + NAT is genuine defense-in-depth (no
route to the internet exists at all, vs. relying on security-group
rules being correct) and is the more conventional choice for
production. Deferred, not rejected — see §14.

**No bastion host.** Admin access to the ClickHouse/RDS boxes goes
through **AWS Systems Manager Session Manager** (SSM), not SSH on port
22. No open inbound port, no bastion EC2 to pay for, full CloudTrail
audit log of every admin session.

## 4. Compute & data tier decisions

- **Backend image**: existing [`Dockerfile`](../Dockerfile) only
  builds the Python side. `app/static/dist` is gitignored (built
  locally via `npm run build` today, per
  [`vite.config.ts`](../frontend/vite.config.ts)'s `outDir`). The
  Dockerfile needs to become a **multi-stage build**: stage 1
  `node:20-slim` runs `npm ci && npm run build` in `frontend/`, stage 2
  is today's Python image, copying the built `dist/` into
  `app/static/dist`. One image, one artifact, no separate frontend
  deployable — consistent with how FastAPI mounts the SPA today.
- **Image tag = git SHA.** Built once on merge to `main`, pushed to
  ECR, the *same* image is promoted dev → staging → prod. Never
  rebuild per environment — what staging validated is bit-for-bit what
  prod runs.
- **ClickHouse**: stays self-hosted on EC2 (`t4g.small` for the
  shared dev/staging box, `t4g.medium` for prod — both ARM/Graviton for
  the price/perf win), EBS gp3 volume, per existing
  `docker-compose.yml` image (`clickhouse/clickhouse-server:24.8`).
  Schema creation already follows the idempotent
  "create on startup if missing" pattern (`CONFIG.md` — no new
  migration tooling needed for CH).
- **Postgres**: RDS `db.t4g.micro`, single-AZ (Multi-AZ ~2x cost, not
  justified yet — documented as the first upgrade if this becomes
  more than a side project), automated backups on, 7-day retention.
  Multiple logical databases on the shared dev/staging instance, not
  multiple RDS instances.
- **Health check**: `GET /health` already exists
  ([`main_api.py:791`](../app/main_api.py)) — used as the ECS task
  health check and the ALB target group health check.

## 4a. Frontend delivery: monolith in dev, split in staging/prod

You asked for production done right from day one but a dev environment
that's lightweight to stand up — those pull in different directions for
the frontend, so this plan deliberately runs **two topologies**, not
one compromise:

- **dev**: keeps today's model — FastAPI serves the built SPA from
  `app/static/dist` (same-origin, no CloudFront, no extra pipeline
  stage). One image, one task, fastest to debug against.
- **staging & prod**: **one CloudFront distribution per environment**,
  with path-based origins — this is the standard AWS reference
  pattern for SPA+API and is strictly better than a separate
  CloudFront-for-frontend / direct-to-ALB-for-API split:

  | Path pattern | Origin |
  |---|---|
  | `/*` (default) | S3 (private bucket, Origin Access Control — no public bucket access) |
  | `/api/*`, `/ws/*`, `/cockpit/*`, `/mcp/*`, `/openapi.json` | ALB → backend Fargate service |

  The backend ships as an **API-only** image (no frontend build
  stage) — static assets come from the edge instead of competing with
  API compute for Fargate cycles, and a frontend-only change deploys
  without touching the backend at all. But because both the SPA and
  the API sit behind the *same* CloudFront domain, **the browser sees
  one origin** — this is the best-practice win: it removes CORS and
  cross-origin config from the picture entirely, not just configures
  around it.

Why staging gets this too, not just prod: staging's job is to
validate the thing that will actually ship. If staging stayed
same-origin monolith, the first time the split topology gets exercised
would be in prod.

**HTTPS without a custom domain.** CloudFront's default certificate
covers its own `*.cloudfront.net` domain automatically, at no extra
cost — set viewer protocol policy to `redirect-to-https` on every
behavior. So the browser-facing leg is HTTPS in staging and prod with
zero domain dependency. The one remaining plain-HTTP hop is
CloudFront→ALB (ALB can't get an ACM cert without a domain to validate
against) — accepted as a standard, low-risk tradeoff: that traffic
never touches the public internet from the *client's* perspective, and
it closes automatically once a domain exists to issue the ALB a cert
against.

**ALB lockdown.** Since CloudFront is now the only legitimate caller,
the ALB security group allows inbound only from AWS's
`com.amazonaws.global.cloudfront.origin-facing` managed prefix list —
not `0.0.0.0/0`. Direct internet access to the ALB's DNS name is
blocked at the network layer, not just "undocumented."

This means the two code changes flagged in the previous draft mostly
fall away:

1. **`frontend/src/api/client.ts`'s `BASE_URL = ""`** (same-origin
   assumption, [`client.ts:5-9`](../frontend/src/api/client.ts)) is
   now *correct* in staging/prod too — same-origin is genuinely true
   again, because CloudFront makes it true. No runtime-config fetch,
   no per-environment build variants needed. **No code change.**
2. **CORS** (`app/main_api.py:565-570`, currently
   `allow_origins=["*"]` + `allow_credentials=True`) is no longer
   load-bearing for browser traffic, since there's no cross-origin
   request to allow. Narrowing it is still worth doing as general
   hardening (any non-CloudFront caller — e.g. a script hitting the
   ALB/MCP surface directly — shouldn't get a wildcard+credentials
   response), but it's optional cleanup now, not a blocking
   prerequisite for this plan.
3. **SPA client-side routing** still needs CloudFront's custom error
   response (403/404 → `/index.html`, 200) since `react-router-dom`
   handles routing client-side and deep links would otherwise 404 at
   S3 before reaching the router.

**WebSocket note.** `/ws` (live bar streaming) needs to keep working
through CloudFront → ALB. CloudFront does pass through WebSocket
upgrades on a standard cache behavior, but origin idle-timeout
settings (CloudFront and ALB both default to short-ish idle timeouts)
need verifying against the actual bar-streaming cadence during
implementation — flagging as a thing to test, not assuming it "just
works."

Cache policy: Vite already content-hashes JS/CSS filenames
(`vite.config.ts` build output), so `/assets/*` is cached at
CloudFront forever (immutable), while `index.html` is no-cache so a
deploy is visible immediately — CI only needs to invalidate
`/index.html` per deploy, not the whole distribution.

## 5. Networking

One VPC, two public subnets (multi-AZ, required for the ALB in
staging+prod), no private subnets / no NAT (see §3). Security groups:

- `sg-alb` (staging + prod): inbound 80 only from AWS's
  `com.amazonaws.global.cloudfront.origin-facing` managed prefix list
  (not `0.0.0.0/0` — CloudFront is the only legitimate caller; see
  §4a), outbound to `sg-backend`.
- `sg-backend`: inbound from `sg-alb` (staging/prod) or nothing (dev —
  direct-to-task access, no ALB needed for the monolith), outbound to
  `sg-clickhouse`, `sg-postgres`, and the internet (provider APIs).
- `sg-clickhouse`, `sg-postgres`: inbound only from `sg-backend` and
  the SSM-managed admin path (no public inbound at all).

## 6. IaC: Terraform

Decided and applied: [`CLAUDE.md`](../CLAUDE.md)'s Infra line now reads
"Terraform for deploy infra... no CDK" (was "No CDK / Terraform").
Terraform's `plan`/`apply` diff is the deciding factor for a one-person
team touching production infra directly — you see exactly what changes
before it happens, which matters more here than CDK's type-safety or
CloudFormation's zero-new-tooling.

- **Module layout**: `infra/terraform/modules/{network,clickhouse,postgres,backend,pipeline}`
  + `infra/terraform/envs/{dev,staging,prod}` (one `.tfvars` per env,
  same modules — no copy-pasted resource blocks across environments).
- **State backend**: S3 bucket + DynamoDB lock table, one per AWS
  account, region `us-east-1` (matches existing `STOCK_LAKE_REGION`
  default). This is the **one explicitly-allowed manual/bootstrap
  step** — Terraform can't create the backend that stores its own
  state from nothing. A `scripts/bootstrap_terraform_backend.sh`
  (idempotent, same style as
  [`provision_lake_infra.sh`](../scripts/provision_lake_infra.sh)) runs
  this once per AWS account, not per deploy.
- **Environments**: separate state files per env directory (not
  Terraform workspaces) — explicit `terraform plan -var-file=envs/prod.tfvars`
  per environment makes it impossible to `apply` against the wrong env
  by forgetting to switch a workspace.

## 7. CI/CD pipeline (GitHub Actions)

```
PR opened/updated:
  - backend: ruff/mypy (if configured) + pytest -m "not integration"
  - frontend: lint, typecheck, build
  - terraform: fmt -check, validate, plan (posted as PR comment, no apply)

merge to main:
  - build dev image (multi-stage: frontend baked in), tag = git SHA, push to ECR
  - build staging/prod image (API-only, no frontend stage), same tag, push to ECR
  - build frontend bundle once (shared by staging + prod, configs differ via runtime config.json — see §4a)
  - terraform apply (shared modules + dev env) — fully automatic
  - deploy dev: update ECS service to new (monolith) image tag, wait for healthy
  - run Alembic migrations against dev Postgres (blocking — failure halts deploy; see §8)

promote to staging (GitHub Environment, manual approval required):
  - terraform apply -var-file=envs/staging.tfvars
  - same API-only image tag (no rebuild) → ECS staging service
  - sync frontend bundle to staging S3 bucket, invalidate /index.html on staging's CloudFront distribution
  - Alembic migration against staging Postgres

promote to prod (GitHub Environment, manual approval required, separate reviewer):
  - terraform apply -var-file=envs/prod.tfvars
  - same API-only image tag → ECS prod service
  - sync frontend bundle to prod S3 bucket, invalidate /index.html on prod's CloudFront distribution
  - Alembic migration against prod Postgres
```

No environment skips a step silently — a failed migration or failed
health check blocks promotion (no-silent-failures standard).

## 8. Database migrations

- **Postgres**: needs Alembic + SQLAlchemy added as dependencies (part
  of the out-of-scope app-integration spec, but the *pipeline step* for
  running migrations is provisioned here so it's ready when that spec
  lands). Migrations run as a one-off ECS task (not inside the
  long-running service container) before the service update, so a
  bad migration fails the deploy instead of crash-looping the app.
- **ClickHouse**: no change — keeps the existing idempotent
  create-on-startup pattern.

## 9. Secrets

AWS Secrets Manager, one secret set per environment (`stockalert/dev/*`,
`/staging/*`, `/prod/*`): `CLICKHOUSE_PASSWORD`, `POSTGRES_PASSWORD`,
`POLYGON_API_KEY`, `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`,
`SCHWAB_CLIENT_ID`/`SCHWAB_CLIENT_SECRET`. ECS task definitions
reference secrets by ARN (injected as env vars at container start, never
baked into the image or committed). `SCHWAB_REFRESH_TOKEN` is
provisioned as a secret too, but see the out-of-scope note in §2 — the
*app* still needs a code change to write rotated tokens back to
Secrets Manager instead of a file.

## 10. Cost estimate (rough, us-east-1, monthly)

| Item | Est. |
|---|---|
| EC2 `t4g.small` (dev+staging ClickHouse) | ~$12 |
| EC2 `t4g.medium` (prod ClickHouse) | ~$24 |
| EBS gp3 (both CH boxes, ~50GB each) | ~$8 |
| RDS `db.t4g.micro` ×2 (shared dev/staging + prod) | ~$24 |
| Fargate ×3 tasks (0.25 vCPU/0.5GB, always-on) | ~$22 |
| ALB ×2 (staging + prod) | ~$32 |
| S3 + CloudFront (staging + prod, low traffic) | ~$3 |
| ECR storage, Secrets Manager, CloudWatch Logs | ~$5 |
| **Total** | **~$130/mo** |

This exceeds the original <$100 target — the split frontend
architecture for staging adds an ALB (~$16) it wouldn't otherwise need
yet, since "staging mirrors prod" was prioritized over "staging is
cheap." If you'd rather staging skip the ALB/CloudFront and stay
same-origin-monolith like dev (accepting that staging then doesn't
validate the split topology before prod sees it), that recovers ~$16/mo
and the spend lands closer to the original target. Your call.

## 11. Phased rollout

1. **Bootstrap**: Terraform state backend (one-time script), amend
   `CLAUDE.md` infra line.
2. **Network + dev**: VPC, security groups, ClickHouse+Postgres shared
   box/instance, dev ECS service (monolith image). Manually verified
   once.
3. **CI/CD pipeline (dev only)**: GitHub Actions build/push/deploy-to-dev,
   Terraform plan-on-PR. Multi-stage Dockerfile for the dev/monolith
   image.
4. **Split-architecture code changes**: `client.ts` runtime config,
   CORS allow-list, CloudFront SPA routing (§4a) — confirmed and built
   before staging exists, since staging depends on them.
5. **Staging**: API-only image, S3+CloudFront, ALB, frontend
   build/deploy pipeline stage, GitHub Environment approval gate.
6. **Prod**: duplicate staging's Terraform env vars, separate reviewer
   gate.
7. **(Separate spec, later)** Postgres app integration, Schwab token
   Secrets Manager rewrite, domain/TLS.

## 12. Decisions resolved (best practice, applied to this draft)

| Decision | Resolution | Why |
|---|---|---|
| IaC tool | Terraform; `CLAUDE.md` amended (§6) | `plan`/`apply` safety for prod changes outweighs CDK type-safety / CloudFormation's zero-tooling for a one-person team |
| Frontend↔API origin model | Single CloudFront distribution, path-based origins (§4a) | Removes CORS and cross-origin config as a *category*, not just a configured workaround; standard AWS reference pattern |
| Browser-facing TLS | CloudFront default cert, `redirect-to-https`, no domain needed (§4a) | Free, zero domain dependency, closes most of the original HTTP gap immediately |
| ALB exposure | Locked to CloudFront's managed prefix list, not `0.0.0.0/0` (§5) | CloudFront is the only legitimate caller now — enforce that at the network layer |
| `client.ts` BASE_URL | No change — stays `""` | Same-origin is genuinely true again under the CloudFront model |
| CORS narrowing | Deferred — optional hardening, not blocking | No longer load-bearing once there's no cross-origin browser request to allow |
| CI/CD runner | GitHub Actions | Repo is already GitHub-hosted and `gh`-driven; one tool end-to-end beats wiring CodePipeline/CodeBuild + a GitHub webhook for the same job |
| Staging frontend topology | Mirrors prod (CloudFront+ALB), not monolith | Staging's job is validating what ships; a cheaper staging that skips the real topology just moves the first real test into prod |

## 13. Remaining items genuinely outside this plan's control

- **Custom domain.** Still not registered. Not blocking (§2, §4a), but
  worth doing eventually for a human-readable URL and to close the
  CloudFront→ALB plain-HTTP hop. Your call on timing — registering a
  domain is a real purchase decision, not something to default into.
- **Cost: ~$130/mo estimate stands** (§10) — the CloudFront
  rearchitecture didn't change it (S3+CloudFront pricing is the same
  either way; the ALB-per-env cost is what staging's prod-fidelity
  goal requires). If $130/mo is a hard ceiling rather than a rough
  budget, staging dropping to monolith-like-dev is still the lever
  that recovers ~$16/mo — say so and I'll fold it in.

## 14. Deferred for cost, not rejected — revisit later

Explicitly chosen to optimize for cost now, with a known, accepted
security tradeoff. Worth a dedicated follow-up spec once cost is less
of a constraint (e.g. real usage justifies the spend, or revenue
exists):

- **Private subnets + NAT Gateway** (§3) — would isolate Fargate
  tasks and the ClickHouse/RDS boxes from any inbound route at the
  network layer, not just security-group rules. Cost: ~$32-35/mo per
  NAT Gateway per environment that gets one.
- **Custom domain + ACM cert on the ALB** (§13) — closes the
  CloudFront→ALB plain-HTTP hop and gives a human-readable URL.
