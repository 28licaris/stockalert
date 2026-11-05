#!/usr/bin/env bash
# Release <sha> to the <env> box: sync infra/dev/ to the ops bucket, then run
# /opt/stockalert/bin/release.sh on the box over SSM (pull → alembic upgrade
# head → compose up → /health). Spec: docs/operations/deployment_dev.md §6.
#
#   scripts/deploy/release.sh <env> <sha>
#
# Also the FIRST deploy: the instance's user-data waits for this sync before it
# runs bootstrap.sh, and this script waits for bootstrap to finish before the
# box-side release runs. Never deletes anything on the box.
set -euo pipefail
SCRIPT_NAME=release
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { echo "usage: $0 <env> <sha>" >&2; exit 2; }
[[ $# -eq 2 ]] || usage
ENV_NAME=$1; SHA=$2
check_env "$ENV_NAME"
[[ "$SHA" =~ ^[0-9a-f]{7,40}(-dirty)?$ ]] || die "sha must be a git short/long sha (build_push.sh prints it), got '$SHA'"
require_tools aws curl python3

cd "$REPO_ROOT"
instance=$(stack_output "$ENV_NAME" InstanceId)
ops_bucket=$(stack_output "$ENV_NAME" OpsBucketName)
repo_uri=$(stack_output "$ENV_NAME" EcrRepositoryUri)
dev_host=$(stack_output "$ENV_NAME" DevHost)
image="$repo_uri:$SHA"
log "env=$ENV_NAME instance=$instance image=$image host=$dev_host"

# 1. The image must exist before we touch the box.
aws ecr describe-images --repository-name "${repo_uri#*/}" --image-ids imageTag="$SHA" \
  --query 'imageDetails[0].imageDigest' --output text >/dev/null 2>&1 \
  || die "$image is not in ECR — run scripts/deploy/build_push.sh $ENV_NAME first"
log "image present in ECR"

# 2. Sync the box config. No --delete: the bucket prefix is ours alone, and the
#    box-side sync must never remove .env/release.env/data.
n_before=$(aws s3api list-objects-v2 --bucket "$ops_bucket" --prefix infra/ --query 'length(Contents || `[]`)' --output text) || die "listing s3://$ops_bucket/infra/ failed"
aws s3 sync "infra/$ENV_NAME/" "s3://$ops_bucket/infra/" --only-show-errors --exclude stack.yaml \
  || die "s3 sync infra/$ENV_NAME/ failed"
n_after=$(aws s3api list-objects-v2 --bucket "$ops_bucket" --prefix infra/ --query 'length(Contents || `[]`)' --output text) || die "listing s3://$ops_bucket/infra/ failed"
log "synced infra/$ENV_NAME/ → s3://$ops_bucket/infra/ (objects before=$n_before after=$n_after)"
(( n_after > 0 )) || die "s3://$ops_bucket/infra/ is empty after sync"

# 3. On the box: wait for bootstrap, refresh config, release.
ssm_run "$ENV_NAME" "$instance" 1800 "release-$SHA" <<EOS
set -euo pipefail
for i in \$(seq 1 90); do [ -f /opt/stockalert/.bootstrapped ] && break; echo "waiting for bootstrap (\$i/90)"; sleep 10; done
[ -f /opt/stockalert/.bootstrapped ] || { echo "ERROR: /opt/stockalert/.bootstrapped missing — see /var/log/stockalert-userdata.log"; exit 1; }
aws s3 sync "s3://$ops_bucket/infra/" /opt/stockalert/ --only-show-errors --exclude stack.yaml --exclude ".env*" --exclude release.env
chmod 0755 /opt/stockalert/bin/*.sh /opt/stockalert/bootstrap.sh
install -m 0644 /opt/stockalert/cron/stockalert /etc/cron.d/stockalert
install -m 0644 /opt/stockalert/systemd/*.service /etc/systemd/system/ && systemctl daemon-reload
/opt/stockalert/bin/release.sh "$image"
EOS

# 4. From the outside, the way friends and Route 53 see it.
body=$(curl -fsS --max-time 15 "https://$dev_host/health") || die "https://$dev_host/health unreachable from here"
log "https://$dev_host/health → $body"
[[ "$body" == *'"status":"ok"'* || "$body" == *'"status": "ok"'* ]] || die "/health is not status=ok"
log "RELEASE OK $image on $ENV_NAME"
