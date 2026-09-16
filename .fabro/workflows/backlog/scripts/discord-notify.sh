#!/bin/sh
# discord-notify.sh — best-effort Discord notification for fabro runs.
#
# Usage: discord-notify.sh <rescue|complete|failed|review-triggered>
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
pr_url=""
review_run_id=""
review_err=""
if [ -r "$token_file" ]; then
  auth="Authorization: Bearer $(cat "$token_file")"
  repo_url=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id" 2>/dev/null \
    | grep -o '"origin_url":"[^"]*"' | head -1 | cut -d'"' -f4)
  # One pass over the (large) state stream for each field we want. Each grep is
  # cut short by head -1, so neither reads the whole body.
  issue=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"issue_number":[0-9]*' | head -1 | cut -d: -f2)
  # open_pr publishes pr_url into the run context once the PR exists; absent for
  # every run that did not get that far.
  pr_url=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"pr_url":"[^"]*"' | head -1 | cut -d'"' -f4)
  # trigger_review's two context keys. Both are always written by that node, so an
  # empty pair means the node skipped (the double-fire guard tripped, or open_pr
  # recorded no usable pr_number) — not that the notification failed.
  review_run_id=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"review_run_id":"[^"]*"' | head -1 | cut -d'"' -f4)
  review_err=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"review_trigger_error":"[^"]*"' | head -1 | cut -d'"' -f4)
  # The reason is server `detail` text, so it can carry a quote, which trigger_review
  # now encodes as \" rather than emitting a broken object. The grep above stops at
  # that quote and hands back the trailing backslash, which would then break the
  # Discord payload the same way. Drop both characters: the message is already
  # truncated at that point and only has to be readable.
  review_err=$(printf '%s' "$review_err" | sed 's/[\\"]//g')
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
  complete) msg="✅ fabro opened a PR${subject}" ;;
  failed)   msg="🔴 fabro run failed${subject}" ;;
  review-triggered)
    # A trigger failure cannot fail the run (trigger_review carries
    # on_failure="succeed"), so `run_failed` never fires for it and this hook is the
    # only place it surfaces. The failure line carries the reason the node captured;
    # a success stays short because `discord-complete` already fired for this PR.
    if [ -n "$review_err" ]; then
      msg="🔴 fabro could not trigger a PR review${subject}: ${review_err}"
    elif [ -n "$review_run_id" ]; then
      msg="🔎 fabro triggered a PR review${subject}"
    else
      exit 0
    fi
    ;;
  *)        msg="ℹ️ fabro run ${run_id} notification ($kind)" ;;
esac

# Links, each added only when we have the pieces for it.
links=""
# The PR is the most useful link when there is one, so it leads.
[ -n "$pr_url" ] && links="${links}\\n${pr_url}"
[ -n "$base_url" ] && links="${links}\\n${base_url}/runs/${run_id}"
# The review this run spawned, on the notification that is about that review. The
# key is only populated once trigger_review has checkpointed, which is after every
# other kind has already fired, so gate on the kind rather than rely on that order
# holding as the graph changes.
if [ "$kind" = "review-triggered" ] && [ -n "$review_run_id" ] && [ -n "$base_url" ]; then
  links="${links}\\n${base_url}/runs/${review_run_id}"
fi
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
