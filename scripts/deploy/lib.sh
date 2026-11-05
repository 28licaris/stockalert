#!/usr/bin/env bash
# Shared helpers for scripts/deploy/*.sh — sourced, not executed.
# Every script: set -euo pipefail, log every outcome, usage on bad args.
[[ -n "${BASH_VERSION:-}" ]] || { echo "lib.sh must be sourced from bash" >&2; exit 2; }

export AWS_PROFILE="${AWS_PROFILE:-stockalert-admin}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

log() { printf '%s %s: %s\n' "$(date -u +%FT%TZ)" "${SCRIPT_NAME:-deploy}" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

require_tools() {
  local missing=()
  for t in "$@"; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
  (( ${#missing[@]} == 0 )) || die "missing tools: ${missing[*]}"
}

# Validate the <env> argument: lowercase, matches the CFN EnvironmentName pattern.
check_env() {
  [[ "${1:-}" =~ ^[a-z0-9-]+$ ]] || die "env must match [a-z0-9-]+ (dev | prod), got '${1:-}'"
}

stack_name() { echo "stockalert-$1"; }

# stack_output <env> <OutputKey> — fails loud if the stack or key is absent.
stack_output() {
  local env=$1 key=$2 val
  val=$(aws cloudformation describe-stacks --stack-name "$(stack_name "$env")" \
          --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" --output text 2>/dev/null) \
    || die "stack $(stack_name "$env") not found (deploy infra/dev/stack.yaml first; profile=$AWS_PROFILE region=$AWS_REGION)"
  [[ -n "$val" && "$val" != "None" ]] || die "stack $(stack_name "$env") has no output '$key'"
  echo "$val"
}

# ssm_run <env> <instance-id> <timeout-seconds> <label> <bash script on stdin>
# Runs a shell script on the box via SSM, streams nothing (SSM is batch), waits,
# prints the box's stdout/stderr, exits non-zero if the command failed.
ssm_run() {
  local env=$1 instance=$2 timeout=$3 label=$4 script cmd_id status
  script=$(cat)
  local ops_bucket; ops_bucket=$(stack_output "$env" OpsBucketName)

  # SSM agent must be Online before send-command is accepted.
  local ping
  ping=$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$instance" \
           --query 'InstanceInformationList[0].PingStatus' --output text)
  [[ "$ping" == "Online" ]] || die "instance $instance SSM PingStatus=$ping (not Online) — wait for the box to boot or check the instance role"

  # Parameters must be JSON; the whole script is one command string.
  local params; params=$(python3 -c 'import json,sys; print(json.dumps({"commands":[sys.stdin.read()]}))' <<< "$script")
  cmd_id=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
             --comment "stockalert $label" --timeout-seconds "$timeout" \
             --parameters "$params" \
             --output-s3-bucket-name "$ops_bucket" --output-s3-key-prefix "ssm-output/$label" \
             --query 'Command.CommandId' --output text) || die "send-command failed"
  log "ssm command $cmd_id sent ($label); waiting up to ${timeout}s"

  # `aws ssm wait command-executed` gives up after ~100 s; poll ourselves.
  local waited=0
  while :; do
    status=$(aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$instance" \
               --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in
      Success|Failed|Cancelled|TimedOut|Cancelling) break ;;
    esac
    (( waited < timeout + 60 )) || { status="LocalTimeout"; break; }
    sleep 5; waited=$((waited + 5))
  done

  echo "──── box stdout ($label) ────"
  aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$instance" \
    --query StandardOutputContent --output text
  echo "──── box stderr ($label) ────"
  aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$instance" \
    --query StandardErrorContent --output text
  echo "─────────────────────────────"
  log "ssm command $cmd_id status=$status (full output: s3://$ops_bucket/ssm-output/$label/$cmd_id/)"
  [[ "$status" == "Success" ]] || die "box command '$label' ended with status=$status"
}
