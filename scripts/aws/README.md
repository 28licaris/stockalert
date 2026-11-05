# scripts/aws/

One-off AWS setup scripts and the IAM policy documents they apply.

Not part of any automated path — there is no CDK or Terraform in this repo, so
these are the record of how AWS was configured by hand.

## Contents

| File | Purpose |
|---|---|
| `upgrade_polygon_depth.sh` | Extend Polygon history depth after a subscription change |
| `iam_policies/stock-lake-rw.json` | Read/write on the lake bucket — the core lake policy |
| `iam_policies/options-glue-rw.json` | Glue grants for the `options` namespace |

## Glue grants are per-namespace

A role with Glue permissions on `equities` **cannot** create or write tables in
`futures` or `options`. This is the most common cause of a backfill failing
late with a permissions error after an hour of successful data movement — check
the namespace grant during preflight, not after.

## Credentials

Lake and S3 access requires `AWS_PROFILE=stock-lake`. A blank ambient
`AWS_PROFILE` makes every lake call hang 45-90 seconds before failing.

The `stock-lake-ingest` user is least-privilege and deliberately **cannot**
list or modify IAM. Role-level inspection needs an admin profile.

## Retired: Spark / EMR Serverless

The EMR Serverless provisioning script and its three Spark IAM policy
documents were deleted on 2026-11-05. Spark and EMR were removed when split
adjustment moved to read-time computation — lake maintenance now runs via
Athena and backfills via CodeBuild. Recover them from git history if ever
needed:

```bash
git log --diff-filter=D -- scripts/aws/setup_emr_serverless.sh
```

> **The AWS-side resources were still live when the files were deleted.** An
> EMR Serverless application `stockalert-spark-batch`
> (`00g5u3fmfj03dg09`, us-east-1) exists in STOPPED state, created
> 2026-05-24, last touched 2026-05-28. Stopped applications bill nothing, but
> it and its `stock-lake-spark-emr` execution role are orphaned IAM/infra
> surface. De-provisioning is tracked in
> [`../../docs/TODO.md`](../../docs/TODO.md) — do it as part of the AWS
> deployment, not piecemeal.
