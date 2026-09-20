#!/bin/sh
# slack-fallback.sh — best-effort Slack alert when a stage leaves the local
# coder box for a hosted model.
#
# Usage: slack-fallback.sh
#
# Runs as a stage_start hook with sandbox = false, i.e. inside the fabro server
# container (Alpine: /bin/sh + wget, no bash, no curl, no jq). The hook context
# JSON is piped to stdin; `node_id` names the escalating stage.
#
# Sources, none of which put a secret in this repo:
#   webhook URL   /storage/secrets/slack_webhook_url   (operator-populated)
#   run link base $FABRO_WEB_URL                        (from ~/fabro/.env)
#   API token     /storage/server.dev-token             (already in the volume)
#   repo + issue  the local fabro API over 127.0.0.1
#
# Every enrichment step is optional: a failed lookup still sends the alert, just
# with less detail. Any failure exits 0 — notification must never fail a run.
set -u

url_file="/storage/secrets/slack_webhook_url"
[ -f "$url_file" ] || exit 0
url="$(cat "$url_file")"
[ -n "$url" ] || exit 0

# The hook context JSON arrives on stdin. Capture it so the shell does not block
# on the pipe.
ctx="$(cat 2>/dev/null || true)"

run_id=$(printf '%s' "$ctx" | grep -o '"run_id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$run_id" ] || run_id="${FABRO_RUN_ID:-?}"

# Nodes of the shared review-merge graph arrive prefixed (`review_merge.ci_fix_t2`),
# so match on the last segment.
node_id=$(printf '%s' "$ctx" | grep -o '"node_id":"[^"]*"' | head -1 | cut -d'"' -f4)
node_short="${node_id##*.}"

# Map the stage to the hosted model it runs on. Anything else is not an
# escalation — including this script being invoked by mistake — and is ignored.
model=""
case "$node_short" in
  rework_t2)       model="glm-4.7" ;;
  rework_t3)       model="kimi-for-coding" ;;
  rework_t4)       model="glm-5.3" ;;
  rebase_agent_t2) model="glm-5.3" ;;
  ci_fix_t2)       model="glm-5.3" ;;
  resolve_merge)   model="glm-5.3" ;;
  *) exit 0 ;;
esac

# ---- enrichment (best effort) -------------------------------------------------
base_url="${FABRO_WEB_URL:-}"
api="http://127.0.0.1:${FABRO_PORT:-32276}"
token_file="/storage/server.dev-token"

repo_url=""
issue=""
if [ -r "$token_file" ]; then
  auth="Authorization: Bearer $(cat "$token_file")"
  repo_url=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id" 2>/dev/null \
    | grep -o '"origin_url":"[^"]*"' | head -1 | cut -d'"' -f4)
  # issue_number is a number in `backlog` and a string in `issue-triage`
  # (`jq --arg`), so accept either spelling. BRE with \{0,1\} and \{1,\} rather
  # than \? and +, because this runs under busybox grep.
  issue=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"issue_number":"\{0,1\}[0-9]\{1,\}' | head -1 | tr -dc '0-9')
fi

repo_name=""
[ -n "$repo_url" ] && repo_name=$(printf '%s' "$repo_url" | sed 's|^https\{0,1\}://[^/]*/||; s|\.git$||')

subject=""
if [ -n "$repo_name" ] && [ -n "$issue" ]; then
  subject=" in ${repo_name}#${issue}"
elif [ -n "$repo_name" ]; then
  subject=" in ${repo_name}"
elif [ -n "$issue" ]; then
  subject=" #${issue}"
fi

msg="fabro model fallback: run ${run_id} escalated to ${model} (${node_short})${subject}"
# Slack incoming webhooks take {"text": "..."}. Strip quotes and backslashes from
# anything that came off the API so it cannot break the JSON string.
msg=$(printf '%s' "$msg" | sed 's/[\\"]//g')

links=""
[ -n "$base_url" ] && links=" ${base_url}/runs/${run_id}"

payload="{\"text\": \"${msg}${links}\"}"
wget -q --post-data="$payload" --header='Content-Type: application/json' -O /dev/null "$url" 2>/dev/null || true

exit 0
