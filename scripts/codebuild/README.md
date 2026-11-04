# scripts/codebuild/

CodeBuild buildspecs for long-running backfills.

## Why CodeBuild and not the dev box

These jobs move tens of gigabytes **between two AWS regions' S3** (Polygon's
flat-file bucket → our lake bucket, both `us-east-1`). Running them from home
pulls every byte down a residential link and back up again. In-region
CodeBuild compute does the same work with no home-internet egress.

There is no standing cluster and no Spark — compute is per-run and
usage-billed.

## Buildspecs

| File | Job |
|---|---|
| `buildspec_polygon_history.yml` | Polygon equities flat-files → `equities.polygon_raw` |
| `buildspec_futures_mirror.yml` | byte-for-byte futures flat-file mirror → `polygon_flatfiles_mirror/` |
| `buildspec_futures_parse_raw.yml` | mirror → `futures.polygon_raw` |
| `buildspec_lake_read_bench.yml` | lake read-path benchmark |
| `buildspec_mcpt_study.yml` | MCPT permutation study (compute-heavy, not data-heavy) |
| `run_polygon_history.sh` | launcher wrapper for the history job |
| `operator-iam-policy.json` | the operator policy needed to start these builds |

Each buildspec's header comment carries its own scope, prerequisites and launch
command — **read it before launching**, since several need a Secrets Manager
entry or an IAM grant that is not obvious from the filename.

## Launching

Builds reuse one CodeBuild project and override the buildspec per run:

```bash
AWS_PROFILE=stockalert-admin aws codebuild start-build \
  --project-name sockalert-silver-full-backfill \
  --buildspec-override scripts/codebuild/buildspec_futures_mirror.yml
```

## Prerequisites that bite

- **Secrets Manager.** The Polygon jobs read `polygon/flatfiles`
  (`access_key_id`, `secret_access_key`). The mirror makes **no** REST calls,
  so it needs no `polygon/api` secret.
- **Service-role grants.** The role needs `s3:PutObject` / `s3:GetObject` /
  `s3:ListBucket` on `STOCK_LAKE_BUCKET` and `secretsmanager:GetSecretValue`
  on the secret. Glue grants are **per-namespace** — the role currently has
  `equities` only, which is what blocks moving the futures nightly here (see
  [`../../docs/TODO.md`](../../docs/TODO.md)).
- **Checksum env.** `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` must be
  set before any AWS client initializes, or large multipart PyIceberg writes
  fail with `BadDigest` — small writes pass, full runs fail.

## Adding one

Copy the closest existing buildspec, keep the header-comment convention
(scope, prerequisites, launch command), and make the job **idempotent and
resumable** — these runs are long enough that they will be interrupted.
Log every outcome including zero, per
[`../../docs/standards/coding.md`](../../docs/standards/coding.md).
