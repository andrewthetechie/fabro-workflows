#!/bin/sh
# discord-notify.sh — best-effort Discord notification for fabro runs.
#
# Usage: discord-notify.sh <rescue|complete|failed>
#
# Runs as a hook with sandbox = false, i.e. inside the fabro server container
# (Alpine: /bin/sh + wget, no bash, no curl, no jq). The event context JSON is
# piped to stdin.
#
# Sources, none of which put a secret in this repo:
#   webhook URL   /storage/secrets/discord_webhook_url   (operator-populated)
#   run link base $FABRO_WEB_URL                         (from ~/fabro/.env)
#   API token     /storage/server.dev-token              (already in the volume)
#   repo + issue  the local fabro API over 127.0.0.1
#
# Hooks inherit the server process environment, so no host address is hard-coded.
# Every enrichment step is optional: if a lookup fails the message still sends,
# just with less detail. Any failure exits 0 — notification must never fail a run.
set -u

kind="${1:-info}"
url_file="/storage/secrets/discord_webhook_url"

[ -f "$url_file" ] || exit 0
url="$(cat "$url_file")"

# Capture stdin (the HookContext JSON) so the shell does not block on the piped
# context; a cancelled run is an operator action, not a failure worth notifying
# about, so the `failed` kind exits silently on a cancel match.
ctx="$(cat 2>/dev/null || true)"
case "$kind" in
  failed)
    if printf '%s' "$ctx" | grep -qi 'cancel'; then exit 0; fi
    ;;
esac

run_id="${FABRO_RUN_ID:-?}"
base_url="${FABRO_WEB_URL:-}"
api="http://127.0.0.1:${FABRO_PORT:-32276}"
token_file="/storage/server.dev-token"

# ---- enrichment (best effort) -------------------------------------------------
# Which repository this run is working, and which issue it claimed. The repo comes
# from the run summary (small); the issue number from the run state, which can be
# megabytes, so it is streamed through grep and cut short by head -1 rather than
# buffered into a variable.
repo_url=""
issue=""
if [ -r "$token_file" ]; then
  auth="Authorization: Bearer $(cat "$token_file")"
  repo_url=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id" 2>/dev/null \
    | grep -o '"origin_url":"[^"]*"' | head -1 | cut -d'"' -f4)
  issue=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"issue_number":[0-9]*' | head -1 | cut -d: -f2)
fi

# repo "owner/name" for display, derived from the origin URL
repo_name=""
if [ -n "$repo_url" ]; then
  repo_name=$(printf '%s' "$repo_url" | sed 's|^https\{0,1\}://[^/]*/||; s|\.git$||')
fi

# ---- message ------------------------------------------------------------------
# "on <repo> #<issue>", degrading cleanly when either lookup came back empty.
subject=""
[ -n "$repo_name" ] && subject=" on $repo_name"
[ -n "$issue" ] && subject="$subject #$issue"

case "$kind" in
  rescue)   msg="🟡 fabro needs a human decision${subject} (rescue gate)" ;;
  complete) msg="✅ fabro finished its work${subject} — opening a PR" ;;
  failed)   msg="🔴 fabro run failed${subject}" ;;
  *)        msg="ℹ️ fabro run ${run_id} notification ($kind)" ;;
esac

# Links, each added only when we have the pieces for it.
links=""
[ -n "$base_url" ] && links="${links}\\n${base_url}/runs/${run_id}"
if [ -n "$repo_url" ] && [ -n "$issue" ]; then
  links="${links}\\n${repo_url}/issues/${issue}"
elif [ -n "$repo_url" ]; then
  links="${links}\\n${repo_url}"
fi

payload="{\"content\": \"${msg}${links}\"}"
if command -v curl >/dev/null 2>&1; then
  curl -fsS -X POST "$url" -H 'Content-Type: application/json' -d "$payload" > /dev/null 2>&1 || true
elif command -v wget >/dev/null 2>&1; then
  wget -q --post-data="$payload" --header='Content-Type: application/json' -O /dev/null "$url" 2>/dev/null || true
fi

exit 0
