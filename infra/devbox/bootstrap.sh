#!/usr/bin/env bash
# Provision the StockAlert devbox (Amazon Linux 2023, arm64) as a dev machine.
# Spec: docs/operations/devbox.md. Run as root by `scripts/devbox/devbox.sh
# provision`, which ships this directory over SSM to $1 and runs it.
# Idempotent: re-run any time to pick up new pins or scripts.
#
# Installs: dev packages, Docker + Compose plugin, Python 3.12, Poetry, Node,
# GitHub CLI, Claude Code, EC2 Instance Connect + sshd drop-in, and the
# devbox-idle-stop timer (the auto-stop).
set -euo pipefail

log() { printf '%s devbox-bootstrap: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"
SRC="${1:?usage: bootstrap.sh <dir holding the infra/devbox files>}"
[[ -f "$SRC/bin/devbox-idle-stop.sh" ]] || die "$SRC does not look like infra/devbox"
DEV_USER=ec2-user
DEV_HOME=/home/$DEV_USER

# Pins — bump deliberately; laptop runs the same majors.
COMPOSE_VERSION=v2.34.0     # = infra/dev/bootstrap.sh
NODE_VERSION=v22.12.0       # frontend engines: node >= 20
POETRY_VERSION=2.1.4        # = laptop `poetry --version`
IDLE_MINUTES="${IDLE_MINUTES:-45}"
NIGHTLY_STOP_HOUR_UTC="${NIGHTLY_STOP_HOUR_UTC:-8}"

# ── 1. Auto-stop FIRST, so a half-provisioned box still stops itself ─────────
install -m 0755 "$SRC/bin/devbox-idle-stop.sh" /usr/local/bin/devbox-idle-stop.sh
install -m 0755 "$SRC/bin/devbox-keep-awake"   /usr/local/bin/devbox-keep-awake
if [[ ! -f /etc/devbox.conf ]]; then   # never clobber on-box tuning on a re-provision
  cat > /etc/devbox.conf <<CONF
IDLE_MINUTES=$IDLE_MINUTES
NIGHTLY_STOP_HOUR_UTC=$NIGHTLY_STOP_HOUR_UTC
CONF
fi
source /etc/devbox.conf
install -m 0644 "$SRC/systemd/devbox-idle-stop.service" /etc/systemd/system/
install -m 0644 "$SRC/systemd/devbox-idle-stop.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now devbox-idle-stop.timer
log "idle-stop timer: $(systemctl is-active devbox-idle-stop.timer) idle_minutes=$IDLE_MINUTES nightly_stop_hour_utc=$NIGHTLY_STOP_HOUR_UTC"
DRY_RUN=1 /usr/local/bin/devbox-idle-stop.sh || die "idle-stop dry run failed"

# ── 2. Packages ──────────────────────────────────────────────────────────────
log "dnf install dev packages"
dnf install -y -q \
  git tmux htop jq tar xz unzip make gcc gcc-c++ \
  python3.12 python3.12-devel python3.12-pip \
  docker ec2-instance-connect >/dev/null
log "git=$(git --version | awk '{print $3}') python3.12=$(python3.12 --version | awk '{print $2}')"

# ── 3. sshd: keys only, fast dead-connection drop (idle timer relies on it) ──
install -m 0600 "$SRC/sshd-devbox.conf" /etc/ssh/sshd_config.d/50-devbox.conf
sshd -t || die "sshd config invalid"
systemctl enable sshd >/dev/null
systemctl reload-or-restart sshd
log "sshd: $(systemctl is-active sshd)"

# ── 4. Docker + Compose plugin ───────────────────────────────────────────────
systemctl enable --now docker
usermod -aG docker "$DEV_USER"
plugin_dir=/usr/local/lib/docker/cli-plugins
install -d -m 0755 "$plugin_dir"
if ! docker compose version 2>/dev/null | grep -q "$COMPOSE_VERSION"; then
  url="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)"
  log "installing compose $COMPOSE_VERSION"
  curl -fsSL "$url" -o "$plugin_dir/docker-compose.tmp" || die "compose download failed: $url"
  chmod 0755 "$plugin_dir/docker-compose.tmp"
  mv -f "$plugin_dir/docker-compose.tmp" "$plugin_dir/docker-compose"
fi
docker compose version | grep -q "$COMPOSE_VERSION" || die "compose is not $COMPOSE_VERSION"
log "docker=$(docker version --format '{{.Server.Version}}') compose=$(docker compose version --short)"

# ── 5. Node (official tarball, checksum-verified) + Claude Code ──────────────
if [[ "$(/usr/local/bin/node --version 2>/dev/null || true)" != "$NODE_VERSION" ]]; then
  tgz="node-${NODE_VERSION}-linux-arm64.tar.xz"
  tmp=$(mktemp -d)
  curl -fsSL "https://nodejs.org/dist/${NODE_VERSION}/${tgz}" -o "$tmp/$tgz" || die "node download failed"
  curl -fsSL "https://nodejs.org/dist/${NODE_VERSION}/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt" || die "node checksums download failed"
  (cd "$tmp" && grep " ${tgz}\$" SHASUMS256.txt | sha256sum -c --quiet -) || die "node checksum mismatch"
  tar -xJf "$tmp/$tgz" -C /usr/local --strip-components=1
  rm -rf "$tmp"
fi
log "node=$(/usr/local/bin/node --version) npm=$(/usr/local/bin/npm --version)"
if ! command -v claude >/dev/null 2>&1; then
  /usr/local/bin/npm install -g --silent @anthropic-ai/claude-code || die "claude code install failed"
fi
log "claude=$(claude --version 2>/dev/null | head -1)"

# ── 6. GitHub CLI ────────────────────────────────────────────────────────────
if ! command -v gh >/dev/null 2>&1; then
  curl -fsSL https://cli.github.com/packages/rpm/gh-cli.repo -o /etc/yum.repos.d/gh-cli.repo || die "gh repo download failed"
  dnf install -y -q gh >/dev/null
fi
log "gh=$(gh --version | head -1 | awk '{print $3}')"

# ── 7. Poetry for the dev user (in-project venv, like the laptop) ────────────
if ! sudo -iu "$DEV_USER" bash -lc "poetry --version 2>/dev/null" | grep -q "$POETRY_VERSION"; then
  log "installing poetry $POETRY_VERSION for $DEV_USER"
  sudo -iu "$DEV_USER" bash -lc "curl -fsSL https://install.python-poetry.org | POETRY_VERSION=$POETRY_VERSION python3.12 -" \
    || die "poetry install failed"
fi
sudo -iu "$DEV_USER" bash -lc "poetry config virtualenvs.in-project true"
log "poetry=$(sudo -iu "$DEV_USER" bash -lc 'poetry --version')"

# ── 8. Dev user shell: PATH, region, GitHub host key ─────────────────────────
cat > /etc/profile.d/stockalert-devbox.sh <<'PROFILE'
# StockAlert devbox (infra/devbox/bootstrap.sh). AWS creds = the instance role;
# never set AWS_PROFILE or access keys here.
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
export STOCKALERT_DEVBOX=1
PROFILE
sudo -u "$DEV_USER" install -d -m 0700 "$DEV_HOME/.ssh"
if ! sudo -u "$DEV_USER" grep -q '^github.com ' "$DEV_HOME/.ssh/known_hosts" 2>/dev/null; then
  ssh-keyscan -t ed25519 github.com 2>/dev/null | sudo -u "$DEV_USER" tee -a "$DEV_HOME/.ssh/known_hosts" >/dev/null
fi
log "github host key pinned: $(sudo -u "$DEV_USER" grep -c '^github.com ' "$DEV_HOME/.ssh/known_hosts")"

# ── 9. Verify the IAM posture the spec promises ──────────────────────────────
ident=$(sudo -iu "$DEV_USER" bash -lc 'aws sts get-caller-identity --query Arn --output text') || die "no instance-role credentials"
[[ "$ident" == *":assumed-role/stockalert-devbox/"* ]] || die "unexpected identity $ident (expected the stockalert-devbox role)"
log "aws identity: $ident"
log "done — disk: $(df -h / | awk 'NR==2{print $4" free of "$2}') mem: $(free -m | awk '/Mem:/{print $2" MiB"}')"
