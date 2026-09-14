#!/bin/sh
# discord-notify.sh — best-effort Discord notification for fabro runs.
#
# Usage: discord-notify.sh <rescue|complete|failed>
#
# Host-side hook script (runs with sandbox = false). The event context JSON is
# piped to stdin. The webhook URL is read from a host file
# (/storage/secrets/discord_webhook_url, populated by the operator) so it never
# lands in git or workflow TOML. Notify is best-effort: if the URL file is
# missing or the POST fails, exit 0 silently.
set -u

kind="${1:-info}"
url_file="/storage/secrets/discord_webhook_url"

[ -f "$url_file" ] || exit 0
url="$(cat "$url_file")"

# Capture stdin (the HookContext JSON) so the shell does not block on the
# piped context; a cancelled run is an operator action, not a failure worth
# notifying about, so the `failed` kind exits silently on a cancel match.
ctx="$(cat 2>/dev/null || true)"
case "$kind" in
  failed)
    if printf '%s' "$ctx" | grep -qi 'cancel'; then exit 0; fi
    ;;
esac

run_id="${FABRO_RUN_ID:-?}"
run_url="http://10.10.0.32:32276/runs/${run_id}"

case "$kind" in
  rescue)   msg="🟡 fabro run ${run_id} needs a human decision (rescue gate)" ;;
  complete) msg="✅ fabro run ${run_id} finished its work — opening a PR" ;;
  failed)   msg="🔴 fabro run ${run_id} failed" ;;
  *)        msg="ℹ️ fabro run ${run_id} notification ($kind)" ;;
esac

payload="{\"content\": \"${msg}  ·  ${run_url}\"}"
if command -v curl >/dev/null 2>&1; then
  curl -fsS -X POST "$url" -H 'Content-Type: application/json' -d "$payload" > /dev/null 2>&1 || true
elif command -v wget >/dev/null 2>&1; then
  wget -q --post-data="$payload" --header='Content-Type: application/json' -O /dev/null "$url" 2>/dev/null || true
fi

exit 0
