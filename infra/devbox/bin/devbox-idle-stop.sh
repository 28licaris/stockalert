#!/usr/bin/env bash
# devbox-idle-stop — stop the devbox when nobody is connected, to save cost.
# Spec: docs/operations/devbox.md ("Auto-stop"). Runs every 5 min from
# devbox-idle-stop.timer; every run logs ONE decision line to the journal:
#   journalctl -u devbox-idle-stop -n 20
#
# "Connected" = an established TCP connection to sshd (VS Code Remote-SSH and
# plain ssh both arrive through the SSM tunnel as a :22 connection) OR a running
# ssm-session-worker (an `aws ssm start-session` shell).
#
# Rules, in order:
#   1. keep-awake file in the future        → keep   (devbox-keep-awake <hours>)
#   2. NIGHTLY_STOP_HOUR_UTC and up ≥ 30 min → stop   (cost cap: a VS Code window
#                                                     left open overnight)
#   3. connected                            → keep   (and stamp last-active)
#   4. idle ≥ IDLE_MINUTES since max(last-active, boot) → stop
set -euo pipefail

CONF=/etc/devbox.conf
[[ -f "$CONF" ]] && source "$CONF"
IDLE_MINUTES="${IDLE_MINUTES:-45}"
NIGHTLY_STOP_HOUR_UTC="${NIGHTLY_STOP_HOUR_UTC:-8}"   # 08 UTC = 03:00 ET (EST)
STATE_DIR=/var/lib/devbox
LAST_ACTIVE="$STATE_DIR/last-active"
KEEP_UNTIL="$STATE_DIR/keep-awake-until"
DRY_RUN="${DRY_RUN:-0}"

log() { echo "devbox-idle-stop: $*"; }

stop_now() {
  log "decision=stop reason=$1 $2"
  if [[ "$DRY_RUN" == 1 ]]; then log "DRY_RUN=1 — not stopping"; exit 0; fi
  wall "devbox: stopping now (reason=$1). Restart with scripts/devbox/devbox.sh start." 2>/dev/null || true  # best-effort notice
  systemctl poweroff
  exit 0
}

install -d -m 0755 "$STATE_DIR"
now=$(date +%s)
btime=$(awk '/^btime/{print $2}' /proc/stat)
[[ "$btime" =~ ^[0-9]+$ ]] || { log "ERROR: cannot read boot time from /proc/stat"; exit 1; }
uptime_min=$(( (now - btime) / 60 ))

ssh_conns=$(ss -Htn state established '( sport = :22 )' | wc -l | tr -d ' ')
ssm_sessions=$(pgrep -c -x ssm-session-worker || true)   # pgrep exits 1 on zero matches; it still prints 0
ssm_sessions="${ssm_sessions:-0}"
active=$(( ssh_conns + ssm_sessions ))

keep_until=0
[[ -f "$KEEP_UNTIL" ]] && keep_until=$(cat "$KEEP_UNTIL")
[[ "$keep_until" =~ ^[0-9]+$ ]] || { log "WARN: bad $KEEP_UNTIL contents '$keep_until' — ignoring"; keep_until=0; }

last=0
[[ -f "$LAST_ACTIVE" ]] && last=$(cat "$LAST_ACTIVE")
[[ "$last" =~ ^[0-9]+$ ]] || last=0
(( last < btime )) && last=$btime            # a fresh boot gets a full idle window
if (( active > 0 )); then
  echo "$now" > "$LAST_ACTIVE"
  last=$now
fi
idle_min=$(( (now - last) / 60 ))

state="ssh=$ssh_conns ssm=$ssm_sessions idle_min=$idle_min limit_min=$IDLE_MINUTES uptime_min=$uptime_min"

if (( keep_until > now )); then
  log "decision=keep reason=keep_awake until=$(date -u -d "@$keep_until" +%FT%TZ) $state"
  exit 0
fi

hour_utc=$(date -u +%-H)
if (( hour_utc == NIGHTLY_STOP_HOUR_UTC && uptime_min >= 30 )); then
  stop_now nightly_cap "$state"
fi

if (( active > 0 )); then
  log "decision=keep reason=connected $state"
  exit 0
fi

if (( idle_min >= IDLE_MINUTES )); then
  stop_now idle "$state"
fi
log "decision=keep reason=idle_below_limit $state"
