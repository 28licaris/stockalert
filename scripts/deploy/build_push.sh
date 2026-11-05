#!/usr/bin/env bash
# Build the arm64 image on the Mac and push it to ECR as :<sha> and :<env>.
# Spec: docs/operations/deployment_dev.md §6.
#
#   scripts/deploy/build_push.sh <env> [--allow-dirty]
#
# Tag = short git SHA of HEAD; the working tree must be clean so the tag means
# what it says (--allow-dirty appends -dirty and warns). Verifies the pushed
# tag exists in ECR before printing the release command.
set -euo pipefail
SCRIPT_NAME=build_push
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { echo "usage: $0 <env> [--allow-dirty]" >&2; exit 2; }
[[ $# -ge 1 && $# -le 2 ]] || usage
ENV_NAME=$1; shift
ALLOW_DIRTY=false
[[ "${1:-}" == "--allow-dirty" ]] && ALLOW_DIRTY=true
[[ $# -le 1 ]] || usage
check_env "$ENV_NAME"
require_tools docker aws git

cd "$REPO_ROOT"
sha=$(git rev-parse --short=12 HEAD)
if [[ -n "$(git status --porcelain)" ]]; then
  $ALLOW_DIRTY || die "working tree is dirty — commit first, or pass --allow-dirty (tag gets -dirty)"
  sha="${sha}-dirty"; log "WARN building a dirty tree as $sha"
fi

repo_uri=$(stack_output "$ENV_NAME" EcrRepositoryUri)
registry=${repo_uri%%/*}
log "env=$ENV_NAME repo=$repo_uri tag=$sha"

aws ecr get-login-password | docker login --username AWS --password-stdin "$registry" >/dev/null \
  || die "ecr login to $registry failed"
log "ecr login ok"

# --provenance=false: a plain single-platform manifest, not an attestation
# index — `docker pull` on the box and ECR's image list both stay simple.
docker buildx build --platform linux/arm64 --provenance=false \
  -t "$repo_uri:$sha" -t "$repo_uri:$ENV_NAME" --push . \
  || die "docker buildx build/push failed"
log "pushed $repo_uri:$sha and :$ENV_NAME"

# Cross-side verify: ECR has the tag, and it is one image.
digest=$(aws ecr describe-images --repository-name "${repo_uri#*/}" --image-ids imageTag="$sha" \
           --query 'imageDetails[0].imageDigest' --output text) || die "describe-images failed"
[[ "$digest" == sha256:* ]] || die "tag $sha not found in ECR after push"
size=$(aws ecr describe-images --repository-name "${repo_uri#*/}" --image-ids imageTag="$sha" \
         --query 'imageDetails[0].imageSizeInBytes' --output text)
log "ECR has $sha digest=${digest:7:12} size_mb=$(( size / 1048576 ))"
log "BUILD OK — next: scripts/deploy/release.sh $ENV_NAME $sha"
