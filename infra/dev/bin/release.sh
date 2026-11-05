#!/usr/bin/env bash
# Box-side release: record the image, re-render .env, pull, migrate, up, verify.
# Invoked by scripts/deploy/release.sh over SSM after it synced infra/dev/ here.
# Spec: docs/operations/deployment_dev.md §6 "release.sh".
#
#   bin/release.sh <image-uri-with-tag>     e.g. 1234.dkr.ecr.us-east-1.amazonaws.com/stockalert/app:abc123
#
# Migrations run in a one-off `api` container BEFORE the new api/worker start,
# never at app startup. Every step logs; any failure exits non-zero so the SSM
# command (and the Mac-side script) report it.
set -euo pipefail

ROOT=${STOCKALERT_ROOT:-/opt/stockalert}
log() { printf '%s release: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
usage() { echo "usage: $0 <image-uri:tag>" >&2; exit 2; }

[[ $# -eq 1 && -n "$1" ]] || usage
IMAGE=$1
[[ "$IMAGE" == *:* && "$IMAGE" != *" "* ]] || die "image must be <registry>/<repo>:<tag>, got '$IMAGE'"

# /etc/stockalert.conf: STOCKALERT_ENV AWS_REGION OPS_BUCKET LOG_GROUP
[[ -f /etc/stockalert.conf ]] || die "/etc/stockalert.conf missing — bootstrap.sh did not run"
set -a; source /etc/stockalert.conf; set +a
export AWS_DEFAULT_REGION="$AWS_REGION"
cd "$ROOT"

log "env=$STOCKALERT_ENV image=$IMAGE"

# 1. Record the image so reboots (systemd → compose up) use the same one.
umask 022
printf 'APP_IMAGE=%s\n' "$IMAGE" > release.env.tmp && mv -f release.env.tmp release.env
log "release.env written"

# 2. Re-render .env (picks up rotated SSM secrets too).
"$ROOT/bin/render_env.sh"

# 3. ECR login with the instance role.
registry=${IMAGE%%/*}
aws ecr get-login-password | docker login --username AWS --password-stdin "$registry" >/dev/null \
  || die "ecr login to $registry failed"
log "ecr login ok ($registry)"

# 4. Pull everything (app image + caddy/clickhouse/postgres updates).
docker compose pull --quiet || die "docker compose pull failed"
log "images pulled: $(docker image inspect "$IMAGE" --format '{{.Id}}' | cut -c8-19)"

# 5. Datastores first, then migrate against them.
docker compose up -d --wait clickhouse postgres || die "clickhouse/postgres did not become healthy"
log "clickhouse + postgres healthy"
docker compose run --rm --no-deps api alembic upgrade head || die "alembic upgrade head failed — api/worker NOT restarted"
log "alembic upgrade head ok"

# 6. Everything else.
docker compose up -d --remove-orphans --wait || die "docker compose up failed"
log "containers: $(docker compose ps --format '{{.Service}}={{.State}}' | tr '\n' ' ')"

# 7. Verify — inside (authoritative) and through Caddy (what Route 53 sees).
docker compose exec -T api python -c "import json,sys,urllib.request; b=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5)); print('api /health:', json.dumps(b)); sys.exit(0 if b.get('status')=='ok' else 3)" \
  || die "api /health is not status=ok (clickhouse unreachable from api?)"

set -a; source "$ROOT/.env"; set +a
for i in $(seq 1 18); do
  if out=$(curl -fsS --max-time 10 "https://${DEV_HOST}/health" 2>&1); then
    log "https://${DEV_HOST}/health → $out"
    # Reboot path: the units were only *enabled* by bootstrap.sh (no image
    # existed then). Starting them now is idempotent and proves §10.11 works.
    systemctl enable --now stockalert-env.service stockalert.service >/dev/null 2>&1 \
      || log "WARN could not enable/start systemd units (not under systemd?)"
    docker image prune -f >/dev/null && log "dangling images pruned"
    log "RELEASE OK image=$IMAGE"
    exit 0
  fi
  log "waiting for https://${DEV_HOST}/health (attempt $i/18: ${out//$'\n'/ })"
  sleep 10
done
die "https://${DEV_HOST}/health never answered 200 — check 'docker compose logs caddy' (ACME) and the security group"
