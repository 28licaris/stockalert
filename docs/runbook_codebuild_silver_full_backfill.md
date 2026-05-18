# Runbook — silver --full backfill via AWS CodeBuild

One-time setup gets the silver backfill running in AWS same-region
as your S3 + Glue. Expected wall-clock: **~15-20 min** for seed × 5y
(vs ~2.5 hr from a residential connection). Cost: ~$1-3 per run.

The artifacts live in
[`scripts/codebuild/`](../scripts/codebuild/):
- `buildspec.yml` — the CodeBuild job (install poetry → run `--full`
  → upload result.json to S3)
- `iam-policy.json` — minimum IAM permissions for the build role

## TL;DR if you've done this before

```bash
aws codebuild start-build --project-name stockalert-silver-full-backfill
# Wait ~15-20 min. Watch in CloudWatch Logs.
# Result JSON: s3://${STOCK_LAKE_BUCKET}/silver_build_reports/codebuild-<build-id>.json
```

---

## First-time setup (~30 min)

### Step 1 — Create a CodeStar Connection to GitHub

CodeBuild needs to pull your repo. The modern way is via a CodeStar
Connection (one-time OAuth):

1. AWS Console → **Developer Tools** → **Settings** → **Connections**
2. Click **Create connection**
3. Provider: **GitHub**. Name: `stockalert-github`
4. Click **Connect to GitHub** → authorize the AWS Connector app in
   the GitHub OAuth popup → install it on your account/org
5. Pick the repo `28licaris/stockalert` (or the org/repo where this lives)
6. Back in AWS, **Connection status** flips to "Available"
7. **Copy the Connection ARN** — you'll paste it into CodeBuild
   in Step 3. Format: `arn:aws:codestar-connections:us-east-1:562741918372:connection/abc123...`

### Step 2 — Create the IAM role for CodeBuild

1. AWS Console → **IAM** → **Roles** → **Create role**
2. Trusted entity type: **AWS service**, use case: **CodeBuild**
3. Skip the permissions wizard (we attach our custom policy in a moment)
4. Role name: `stockalert-codebuild-silver-role`
5. Create the role
6. Open the role → **Permissions** → **Add permissions** →
   **Create inline policy** → switch to **JSON** tab
7. Paste the contents of [`scripts/codebuild/iam-policy.json`](../scripts/codebuild/iam-policy.json)
   (strip the `_comment` and `_metadata` keys — AWS rejects unknown keys)
8. Name the policy `silver-backfill-permissions`. Save.

**Quick way to strip the comments locally before pasting:**
```bash
poetry run python -c "
import json
with open('scripts/codebuild/iam-policy.json') as f:
    data = json.load(f)
# Strip _comment / _metadata keys from the top level + each statement
data.pop('_metadata', None)
for s in data.get('Statement', []):
    s.pop('_comment', None)
print(json.dumps(data, indent=2))
" | pbcopy
# (now paste into AWS Console)
```

### Step 3 — Create the CodeBuild project

1. AWS Console → **CodeBuild** → **Build projects** → **Create build project**
2. **Project configuration**:
   - Name: `stockalert-silver-full-backfill`
   - Description: "Silver OHLCV --full backfill from bronze (TA-5.1.7)"
3. **Source**:
   - Source provider: **GitHub** (via connection)
   - Connection: select the connection ARN from Step 1
   - Repository: `28licaris/stockalert`
   - Branch: `main`
4. **Environment**:
   - Environment image: **Managed image**
   - Operating system: **Amazon Linux 2023** (or Ubuntu — both work)
   - Runtime: **Standard**
   - Image: latest aws/codebuild/al2023-x86_64-standard or aws/codebuild/standard:7.0
   - Image version: latest
   - Compute: **3 GB memory, 2 vCPUs** is plenty (the build is I/O bound, not CPU)
   - Service role: **Existing service role** → pick
     `stockalert-codebuild-silver-role` from Step 2
5. **Buildspec**:
   - Build specifications: **Use a buildspec file**
   - Buildspec name: `scripts/codebuild/buildspec.yml`
6. **Logs**:
   - CloudWatch logs: **Enabled** (default)
7. **Environment variables** (optional, defaults in buildspec are fine):
   - `STOCK_LAKE_BUCKET`: your bucket name (default matches your `.env`)
   - `AWS_REGION`: `us-east-1`
   - `SILVER_BUILD_SYMBOLS`: `active` (or `seed`, or comma-separated)
   - `SILVER_BUILD_MODE`: `full` (or `nightly`)
   - `SILVER_BUILD_SINCE` / `SILVER_BUILD_UNTIL`: leave empty for default window
8. Click **Create build project**

---

## Run the build

Two ways:

### A — AWS Console
1. CodeBuild → Build projects → `stockalert-silver-full-backfill`
2. Click **Start build**
3. Click **Phase details** + **CloudWatch Logs** to watch live output

### B — AWS CLI
```bash
aws codebuild start-build --project-name stockalert-silver-full-backfill

# Get the build ID it returned, then tail logs:
aws codebuild batch-get-builds --ids <build-id> --query 'builds[0].logs.cloudWatchLogs'
# Open that log group + log stream in the AWS Console, or:
aws logs tail /aws/codebuild/stockalert-silver-full-backfill --follow
```

---

## What to expect

Phases the buildspec runs:

```
install      ~30-60 sec   pip install poetry + dependencies (~150 packages)
pre_build    ~5 sec       sanity-check S3 + Glue reachability
build        ~10-20 min   the actual silver --full backfill
post_build   ~5 sec       upload result.json to S3, print summary
```

**Logs to watch for:**
- `loaded 5108 corp_actions rows; 32 symbols have splits` — corp_actions cache primed
- `write_strategy=append` — confirms the auto-detect-empty fixed-table path
- `month=YYYY-MM wrote NNN ohlcv rows (append)` — per-month progress
- Final summary: `status: ok`, `silver_rows: ~30,000,000`

**If the build fails:**
- IAM permission denied → re-check Step 2's inline policy
- Glue table not found → check `ICEBERG_GLUE_DATABASE` env var matches your `.env`
- Bronze tables empty → verify by running `aws s3 ls s3://$BUCKET/iceberg/bronze/`

---

## After the build succeeds

The CodeBuild project uploads the run summary to S3:

```bash
# List recent reports
aws s3 ls s3://stock-lake-562741918372-us-east-1-an/silver_build_reports/

# Download the latest
aws s3 cp s3://stock-lake-562741918372-us-east-1-an/silver_build_reports/codebuild-<id>.json ./silver_full.json
jq '{status, result: {slices, slices_succeeded, silver_rows, duration_seconds}}' silver_full.json
```

**Then proceed with the validation steps from
[runbook_silver_ohlcv_build.md](runbook_silver_ohlcv_build.md):**

1. Run `scripts/verify_silver_build.py --since 2021-01-04` locally
   (it reads silver via PyIceberg, same as the build did)
2. Yahoo spot-check on NVDA 2024-06-07 (should return ~120.88)
3. Flip `SILVER_OHLCV_BUILD_ENABLED=true` +
   `SILVER_DERIVED_ADD_MEMBERS_ENABLED=true` in your `.env`, restart
   FastAPI
4. Test the add-symbol flow on a throwaway watchlist symbol

---

## Re-running

Same `aws codebuild start-build` command. The build is idempotent:
- First run with empty silver: uses `append` (fast)
- Re-runs over non-empty silver: auto-detects, uses `upsert` (slower
  but byte-identical output)

For partial rebuilds, override the window:
```bash
aws codebuild start-build \
  --project-name stockalert-silver-full-backfill \
  --environment-variables-override \
    name=SILVER_BUILD_SINCE,value=2024-06-01 \
    name=SILVER_BUILD_UNTIL,value=2024-06-30 \
    name=SILVER_BUILD_MODE,value=""
```

(`SILVER_BUILD_MODE=""` makes the script use `--since`/`--until`
instead of `--full`/`--nightly`.)

---

## Cost notes

- CodeBuild general1.small: $0.005/build-minute. 20-min build = ~$0.10
- Outbound data transfer: ~$0 (everything stays in us-east-1)
- S3 PUT/GET costs: ~$0.01 for the build's metadata churn
- CloudWatch Logs storage: $0.50/GB-month (negligible at our log volume)

**Total per --full run: ~$0.50 to $1.50.** Per nightly delta if you
ever route those through CodeBuild instead of the in-process loop:
~$0.02-0.05.

---

## Cleanup (when you're done with everything)

```bash
# Delete the build project
aws codebuild delete-project --name stockalert-silver-full-backfill

# Detach + delete the IAM role
aws iam delete-role-policy --role-name stockalert-codebuild-silver-role \
  --policy-name silver-backfill-permissions
aws iam delete-role --role-name stockalert-codebuild-silver-role

# Delete the CodeStar Connection (Console only)
# Developer Tools → Settings → Connections → select → Delete

# Old build reports in S3
aws s3 rm s3://stock-lake-562741918372-us-east-1-an/silver_build_reports/ --recursive
```

Keep the connection + role if you'll re-run periodically.
