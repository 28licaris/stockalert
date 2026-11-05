#!/usr/bin/env bash
# First-boot provisioning for the StockAlert dev box (Amazon Linux 2023, arm64).
# Spec: docs/operations/deployment_dev.md §7.
#
# Called by the EC2 user-data shim in stack.yaml AFTER it wrote
# /etc/stockalert.conf and synced s3://<ops-bucket>/infra/ → /opt/stockalert.
# Idempotent: re-running on a provisioned box is a no-op apart from log lines.
# Installs: Docker + Compose plugin, 2 GiB swapfile, cronie + cron file,
# CloudWatch agent (disk/mem metrics), systemd units (enabled, NOT started —
# the first bin/release.sh starts them once an image exists).
set -euo pipefail

log() { printf '%s bootstrap: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"
[[ -f /etc/stockalert.conf ]] || die "/etc/stockalert.conf missing — the user-data shim writes it"
set -a; source /etc/stockalert.conf; set +a
: "${STOCKALERT_ENV:?}" "${AWS_REGION:?}" "${OPS_BUCKET:?}" "${LOG_GROUP:?}"
ROOT=/opt/stockalert
cd "$ROOT"

COMPOSE_VERSION=v2.34.0   # pinned; Mac runs the same major/minor (docker compose version)

# ── 1. Packages ───────────────────────────────────────────────────────────────
log "dnf install docker cronie amazon-cloudwatch-agent"
dnf install -y -q docker cronie amazon-cloudwatch-agent >/dev/null
systemctl enable --now crond
log "crond active: $(systemctl is-active crond)"

# ── 2. Docker daemon + Compose plugin ────────────────────────────────────────
install -d -m 0755 /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "live-restore": true
}
JSON
systemctl enable --now docker
log "docker active: $(systemctl is-active docker) version=$(docker version --format '{{.Server.Version}}')"

plugin_dir=/usr/local/lib/docker/cli-plugins
install -d -m 0755 "$plugin_dir"
if ! docker compose version 2>/dev/null | grep -q "$COMPOSE_VERSION"; then
  arch=$(uname -m)   # aarch64 on Graviton
  url="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${arch}"
  log "installing compose plugin $COMPOSE_VERSION from $url"
  curl -fsSL "$url" -o "$plugin_dir/docker-compose.tmp" || die "compose plugin download failed"
  chmod 0755 "$plugin_dir/docker-compose.tmp"
  mv -f "$plugin_dir/docker-compose.tmp" "$plugin_dir/docker-compose"
fi
docker compose version | grep -q "$COMPOSE_VERSION" || die "docker compose is not $COMPOSE_VERSION after install"
log "compose: $(docker compose version --short)"

# ── 3. Swapfile (2 GiB) — t4g.large is tight at ~7 GiB committed ─────────────
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  if [[ ! -f /swapfile ]]; then
    fallocate -l 2G /swapfile || die "fallocate /swapfile failed"
    chmod 0600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
log "swap: $(swapon --show=NAME,SIZE --noheadings | tr '\n' ' ')"

# ── 4. Files, permissions, cron ──────────────────────────────────────────────
chmod 0755 "$ROOT" "$ROOT/bin"/*.sh "$ROOT/bootstrap.sh"
install -m 0644 "$ROOT/cron/stockalert" /etc/cron.d/stockalert
log "cron installed: $(grep -cE '^[0-9*]' /etc/cron.d/stockalert) active job(s)"

# ── 5. CloudWatch agent: disk + memory → CWAgent namespace (stack.yaml alarms)
cw_cfg=/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
cat > "$cw_cfg" <<'JSON'
{
  "agent": { "metrics_collection_interval": 60 },
  "metrics": {
    "namespace": "CWAgent",
    "append_dimensions": { "InstanceId": "${aws:InstanceId}" },
    "aggregation_dimensions": [["InstanceId"]],
    "metrics_collected": {
      "disk": { "resources": ["/"], "measurement": ["used_percent"], "drop_device": true },
      "mem":  { "measurement": ["used_percent"] },
      "swap": { "measurement": ["used_percent"] }
    }
  }
}
JSON
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -c "file:$cw_cfg" -s >/dev/null
log "cloudwatch agent: $(systemctl is-active amazon-cloudwatch-agent)"

# ── 6. systemd units (enabled for reboot; started by the first release) ──────
install -m 0644 "$ROOT/systemd/stockalert-env.service" "$ROOT/systemd/stockalert.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable stockalert-env.service stockalert.service
log "units enabled: $(systemctl is-enabled stockalert-env.service) $(systemctl is-enabled stockalert.service)"

date -u +%FT%TZ > "$ROOT/.bootstrapped"
log "BOOTSTRAP OK env=$STOCKALERT_ENV root=$ROOT — next: scripts/deploy/release.sh $STOCKALERT_ENV <sha> from the Mac"
