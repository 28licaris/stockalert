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
| `iam_policies/stock-lake-ingest-emr-additions.json` | **Retired** — EMR-era additions |
| `iam_policies/spark-emr-execution-role-*.json` | **Retired** — EMR execution role + trust policy |
| `setup_emr_serverless.sh` | **Retired** — EMR Serverless provisioning |

> **The EMR files are retired, not current.** Spark and EMR Serverless were
> removed when split adjustment moved to read time; lake maintenance now runs
> via Athena and backfills via CodeBuild. They are kept only as a record of
> what was provisioned. Do not use them to set up a new environment.

## Glue grants are per-namespace

A role with Glue permissions on `equities` **cannot** create or write tables in
`futures` or `options`. This is the most common cause of a backfill failing
late with a permissions error after an hour of successful data movement — check
the namespace grant during preflight, not after.

## Credentials

Lake and S3 access requires `AWS_PROFILE=stock-lake`. A blank ambient
`AWS_PROFILE` makes every lake call hang 45-90 seconds before failing.
