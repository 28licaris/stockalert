#!/usr/bin/env bash
# devbox.sh — laptop-side control for the StockAlert devbox (docs/operations/devbox.md).
#
#   deploy              create/update the stockalert-devbox CloudFormation stack
#   provision           ship infra/devbox/ over SSM and run bootstrap.sh (idempotent)
#   ssh-config          write ~/.ssh/config "Host stockalert-devbox" + a proxy that
#                       auto-starts the box (then: VS Code → Remote-SSH → stockalert-devbox)
#   clone               git clone the repo on the box (uses your forwarded GitHub key)
#   push-env            copy the laptop .env minus AWS creds + infra/devbox/devbox.env
#                       overrides, plus the Schwab refresh-token file
#   start | stop | status
#   shell               plain SSM shell (no ssh needed)
#   keep-awake <h|off>  hold off the auto-stop for <h> hours (1-24)
set -euo pipefail
SCRIPT_NAME=devbox
source "$(dirname "${BASH_SOURCE[0]}")/../deploy/lib.sh"

STACK=stockalert-devbox
HOST_ALIAS=stockalert-devbox
DEV_USER=ec2-user
KEY="$HOME/.ssh/stockalert-devbox"
PROXY="$HOME/.ssh/stockalert-devbox-proxy.sh"
GIT_URL="git@github.com:28licaris/stockalert.git"
# The laptop .env lives in the MAIN checkout (worktrees have none).
MAIN_REPO="$(cd "$(git -C "$REPO_ROOT" rev-parse --git-common-dir)/.." && pwd)"

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

instance_id() { stack_output devbox InstanceId; }

instance_state() {
  aws ec2 describe-instances --instance-ids "$1" \
    --query 'Reservations[0].Instances[0].State.Name' --output text
}

wait_ssm_online() {
  local id=$1 ping=""
  for _ in $(seq 1 60); do
    ping=$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$id" \
             --query 'InstanceInformationList[0].PingStatus' --output text)
    [[ "$ping" == "Online" ]] && { log "ssm agent Online"; return 0; }
    sleep 5
  done
  die "ssm agent not Online after 300 s (ping=$ping)"
}

cmd_deploy() {
  require_tools aws
  local vpc subnet
  vpc=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
  [[ "$vpc" == vpc-* ]] || die "no default VPC found (got '$vpc')"
  subnet=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" "Name=availability-zone,Values=${AWS_REGION}a" \
             --query 'Subnets[0].SubnetId' --output text)
  [[ "$subnet" == subnet-* ]] || die "no subnet in ${AWS_REGION}a of $vpc (got '$subnet')"
  log "deploying $STACK vpc=$vpc subnet=$subnet ${*:+overrides: $*}"
  aws cloudformation deploy --stack-name "$STACK" \
    --template-file "$REPO_ROOT/infra/devbox/stack.yaml" \
    --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
    --tags application=stockalert environment=devbox \
    --parameter-overrides "VpcId=$vpc" "SubnetId=$subnet" "$@"
  log "stack $(aws cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].StackStatus' --output text) instance=$(instance_id)"
}

cmd_provision() {
  require_tools aws tar base64 python3
  local id; id=$(instance_id)
  [[ "$(instance_state "$id")" == running ]] || cmd_start
  wait_ssm_online "$id"
  local payload params cmd_id status waited=0
  payload=$(COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -C "$REPO_ROOT/infra" -czf - devbox | base64 | tr -d '\n')
  local script="set -euo pipefail
rm -rf /tmp/devbox-src && mkdir -p /tmp/devbox-src
echo '$payload' | base64 -d | tar -xzf - -C /tmp/devbox-src
bash /tmp/devbox-src/devbox/bootstrap.sh /tmp/devbox-src/devbox"
  params=$(python3 -c 'import json,sys; print(json.dumps({"commands":[sys.stdin.read()]}))' <<< "$script")
  cmd_id=$(aws ssm send-command --instance-ids "$id" --document-name AWS-RunShellScript \
             --comment "stockalert devbox provision" --timeout-seconds 1800 \
             --parameters "$params" --query 'Command.CommandId' --output text) || die "send-command failed"
  log "provision command $cmd_id sent; waiting (first run ~5-10 min)"
  while :; do
    status=$(aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$id" \
               --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in Success|Failed|Cancelled|TimedOut) break ;; esac
    (( waited < 1900 )) || { status=LocalTimeout; break; }
    sleep 10; waited=$((waited + 10))
  done
  echo "──── devbox stdout ────"
  aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$id" --query StandardOutputContent --output text
  echo "──── devbox stderr ────"
  aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$id" --query StandardErrorContent --output text
  echo "───────────────────────"
  log "provision status=$status (SSM keeps only the last 24 KB of output above)"
  [[ "$status" == Success ]] || die "provision failed"
}

cmd_ssh_config() {
  require_tools aws ssh-keygen session-manager-plugin
  local id; id=$(instance_id)
  if [[ ! -f "$KEY" ]]; then
    ssh-keygen -q -t ed25519 -N "" -C "stockalert-devbox $(whoami)@$(hostname -s)" -f "$KEY"
    log "created key $KEY (pushed for 60 s per connect via EC2 Instance Connect; never stored on the box)"
  fi
  cat > "$PROXY" <<PROXY
#!/usr/bin/env bash
# Generated by scripts/devbox/devbox.sh ssh-config — ssh ProxyCommand for $HOST_ALIAS.
# Starts the devbox if it is stopped, pushes a 60-second key, opens an SSM tunnel.
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE}" AWS_REGION="${AWS_REGION}" AWS_PAGER=""
id="\$1"; port="\$2"
log() { echo "devbox-proxy: \$*" >&2; }
state=\$(aws ec2 describe-instances --instance-ids "\$id" --query 'Reservations[0].Instances[0].State.Name' --output text)
log "instance \$id state=\$state"
if [[ "\$state" == stopping ]]; then
  log "devbox is stopping; waiting for it to finish before starting it again"
  aws ec2 wait instance-stopped --instance-ids "\$id"; state=stopped
fi
case "\$state" in
  running) ;;
  pending) aws ec2 wait instance-running --instance-ids "\$id" ;;
  stopped) log "starting the devbox (~1-2 min)"
           aws ec2 start-instances --instance-ids "\$id" >/dev/null
           aws ec2 wait instance-running --instance-ids "\$id"
           sleep 20 ;;   # SSM ping can read a stale Online right after start
  *) log "ERROR: cannot connect in state \$state"; exit 1 ;;
esac
ping=""
for _ in \$(seq 1 60); do
  ping=\$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=\$id" --query 'InstanceInformationList[0].PingStatus' --output text)
  [[ "\$ping" == Online ]] && break
  sleep 3
done
[[ "\$ping" == Online ]] || { log "ERROR: ssm agent not Online (ping=\$ping)"; exit 1; }
aws ec2-instance-connect send-ssh-public-key --instance-id "\$id" --instance-os-user $DEV_USER \
  --ssh-public-key "file://$KEY.pub" --query Success --output text >/dev/null
exec aws ssm start-session --target "\$id" --document-name AWS-StartSSHSession --parameters "portNumber=\$port"
PROXY
  chmod 0755 "$PROXY"

  local cfg="$HOME/.ssh/config" begin="# >>> stockalert-devbox (scripts/devbox/devbox.sh ssh-config) >>>" end="# <<< stockalert-devbox <<<"
  touch "$cfg"; chmod 0600 "$cfg"
  # Replace our marked block in place; leave every other line untouched.
  python3 - "$cfg" "$begin" "$end" <<'PY'
import sys
path, begin, end = sys.argv[1:4]
lines = open(path).read().splitlines(keepends=True)
out, skip = [], False
for line in lines:
    if line.rstrip("\n") == begin: skip = True; continue
    if skip and line.rstrip("\n") == end: skip = False; continue
    if not skip: out.append(line)
open(path, "w").write("".join(out).rstrip("\n") + ("\n\n" if out else ""))
PY
  cat >> "$cfg" <<CFG
$begin
Host $HOST_ALIAS
  HostName $id
  User $DEV_USER
  IdentityFile $KEY
  IdentitiesOnly yes
  ForwardAgent yes
  ServerAliveInterval 30
  ServerAliveCountMax 4
  StrictHostKeyChecking accept-new
  ProxyCommand $PROXY %h %p
$end
CFG
  log "wrote Host $HOST_ALIAS → $id in $cfg (proxy: $PROXY)"
  log "test: ssh $HOST_ALIAS 'hostname; uptime'"
}

cmd_clone() {
  ssh-add -l >/dev/null 2>&1 || die "no key in your ssh-agent — run: ssh-add --apple-use-keychain ~/.ssh/id_rsa"
  ssh -A "$HOST_ALIAS" "set -euo pipefail
    if [[ -d ~/stockalert/.git ]]; then echo 'repo already cloned:'; git -C ~/stockalert log --oneline -1
    else git clone $GIT_URL ~/stockalert && git -C ~/stockalert log --oneline -1; fi
    cd ~/stockalert && poetry env use python3.12 >/dev/null && echo \"poetry venv: \$(poetry run python --version)\""
  log "clone ok — next: devbox.sh push-env, then on the box: poetry install"
}

cmd_push_env() {
  local src="$MAIN_REPO/.env" overrides="$REPO_ROOT/infra/devbox/devbox.env" tmp
  [[ -f "$src" ]] || die "no laptop .env at $src"
  tmp=$(mktemp); trap 'rm -f "$tmp"' RETURN
  python3 - "$src" "$overrides" "$tmp" <<'PY'
import re, sys
src, ovr, out = sys.argv[1:4]
key = lambda l: (m.group(1) if (m := re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", l)) else None)
ovr_lines = open(ovr).read().splitlines()
ovr_keys = {k for l in ovr_lines if (k := key(l))}
kept = dropped_aws = dropped_ovr = 0
body = []
for l in open(src).read().splitlines():
    k = key(l)
    if k and k.startswith("AWS_"): dropped_aws += 1; continue
    if k and k in ovr_keys: dropped_ovr += 1; continue
    body.append(l); kept += k is not None
open(out, "w").write("\n".join(body + [""] + ovr_lines) + "\n")
print(f"push-env: kept={kept} dropped_aws={dropped_aws} replaced_by_overrides={dropped_ovr} overrides={len(ovr_keys)}")
PY
  ! grep -qE '^\s*(export\s+)?AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN|PROFILE)\s*=' "$tmp" \
    || die "refusing: AWS credentials survived the filter"
  grep -qx 'STREAM_PROVIDER=none' "$tmp" || die "refusing: STREAM_PROVIDER=none missing from the devbox .env"
  ssh "$HOST_ALIAS" 'test -d ~/stockalert' || die "repo not on the box — run: devbox.sh clone"
  scp -q "$tmp" "$HOST_ALIAS:stockalert/.env"
  ssh "$HOST_ALIAS" 'chmod 600 ~/stockalert/.env && mkdir -p ~/stockalert/data'
  log "pushed .env → $HOST_ALIAS:~/stockalert/.env"
  if [[ -f "$MAIN_REPO/data/.schwab_refresh_token" ]]; then
    scp -q "$MAIN_REPO/data/.schwab_refresh_token" "$HOST_ALIAS:stockalert/data/.schwab_refresh_token"
    ssh "$HOST_ALIAS" 'chmod 600 ~/stockalert/data/.schwab_refresh_token'
    log "pushed Schwab refresh token (REST only on the devbox; re-auth stays on the laptop via push_schwab_token.sh)"
  else
    log "no laptop Schwab token file — Schwab REST calls on the devbox will fail until you push one"
  fi
}

cmd_start() {
  local id; id=$(instance_id)
  local st; st=$(instance_state "$id")
  log "instance $id state=$st"
  if [[ "$st" != running ]]; then
    aws ec2 start-instances --instance-ids "$id" >/dev/null
    aws ec2 wait instance-running --instance-ids "$id"
    log "running"
  fi
}

cmd_stop() {
  local id; id=$(instance_id)
  aws ec2 stop-instances --instance-ids "$id" >/dev/null
  log "stop requested for $id (state now $(instance_state "$id"))"
}

cmd_status() {
  local id; id=$(instance_id)
  local st ping alarm itype
  st=$(instance_state "$id")
  itype=$(aws ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0].InstanceType' --output text)
  ping=$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$id" \
           --query 'InstanceInformationList[0].PingStatus' --output text)
  alarm=$(aws cloudwatch describe-alarms --alarm-names stockalert-devbox-idle-backstop \
           --query 'MetricAlarms[0].StateValue' --output text)
  log "instance=$id type=$itype state=$st ssm=$ping backstop_alarm=$alarm"
  if [[ "$st" == running && "$ping" == Online && -f "$KEY" ]]; then
    ssh -o ConnectTimeout=60 "$HOST_ALIAS" 'journalctl -u devbox-idle-stop -n 1 -o cat --no-pager; devbox-keep-awake status' \
      || log "could not read idle-stop journal over ssh"
  fi
}

cmd_shell() { exec aws ssm start-session --target "$(instance_id)"; }

cmd_keep_awake() {
  [[ -n "${1:-}" ]] || die "usage: devbox.sh keep-awake <1-24 hours> | off"
  ssh "$HOST_ALIAS" "devbox-keep-awake $1"
}

require_tools aws
case "${1:-}" in
  deploy)     shift; cmd_deploy "$@" ;;
  provision)  cmd_provision ;;
  ssh-config) cmd_ssh_config ;;
  clone)      cmd_clone ;;
  push-env)   cmd_push_env ;;
  start)      cmd_start ;;
  stop)       cmd_stop ;;
  status)     cmd_status ;;
  shell)      cmd_shell ;;
  keep-awake) shift; cmd_keep_awake "${1:-}" ;;
  *)          usage ;;
esac
