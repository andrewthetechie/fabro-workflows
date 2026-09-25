#!/bin/sh
# discord-notify.sh — best-effort Discord notification for fabro runs.
#
# Usage: discord-notify.sh <rescue|complete|failed|merged|blocked|needs-human|fallback|triage-question|triage-failed>
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

# A `fallback` alert (stage_start on an escalation tier) names the hosted model
# the stage moved to. The mapping lives here, not in the hook matcher: one hook
# covers several nodes with different models. Imported nodes arrive prefixed
# (`review_merge.ci_fix_t2`), so key on the last segment. An unknown node is not
# an escalation -- exit without a message rather than send a vague one.
#
# The pattern allows whitespace around the colon. The enrichment greps below read
# the fabro HTTP API, whose JSON is compact and proven in production; this one reads
# the HookContext piped to stdin, which is a different producer that nothing here
# has observed. `"node_id": "x"` with a space would yield an empty id, and an empty
# id takes the `*)` branch and exits 0 -- a silent no-message, which is the same
# failure shape as the unprefixed matcher this hook was written to avoid. `cut -f4`
# is already space-tolerant, so only the pattern needed widening.
model=""
node_short=""
if [ "$kind" = "fallback" ]; then
  node_id=$(printf '%s' "$ctx" | grep -o '"node_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
  node_short="${node_id##*.}"
  case "$node_short" in
    rework_t2)       model="glm-5.3-flash" ;;
    rework_t3)       model="kimi-for-coding" ;;
    rework_t4)       model="glm-5.3" ;;
    rebase_agent_t2) model="glm-5.3" ;;
    ci_fix_t2)       model="glm-5.3" ;;
    resolve_merge)   model="glm-5.3" ;;
    *) exit 0 ;;
  esac
fi

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
triage_questions=""
issue_url=""
if [ -r "$token_file" ]; then
  auth="Authorization: Bearer $(cat "$token_file")"
  repo_url=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id" 2>/dev/null \
    | grep -o '"origin_url":"[^"]*"' | head -1 | cut -d'"' -f4)
  # One pass over the (large) state stream for each field we want. Each grep is
  # cut short by head -1 or tail -1, so neither reads the whole body.
  # Both spellings, deliberately. `backlog` publishes issue_number as a NUMBER
  # (2599); `issue-triage`'s claim builds it with `jq --arg`, so it arrives as a
  # STRING ("1195") and the old `:[0-9]*` pattern matched zero digits — every triage
  # notification silently lost its "#1195" and its issue link, which are the two
  # things that make the 30-minute question ping actionable. BRE with \{0,1\} and
  # \{1,\} rather than \? and +, because this runs under busybox grep.
  issue=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"issue_number":"\{0,1\}[0-9]\{1,\}' | head -1 | tr -dc '0-9')
  # open_pr publishes pr_url into the run context once the PR exists; absent for
  # every run that did not get that far.
  pr_url=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"pr_url":"[^"]*"' | head -1 | cut -d'"' -f4)
  # tail -1, not head -1: `checkpoints` in the state is an ascending array, so the
  # LAST match is the newest value. A run that triages many issues publishes these
  # keys once per issue, and head -1 would name the first issue every time.
  triage_questions=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"triage_questions":"[^"]*"' | tail -1 | cut -d'"' -f4)
  issue_url=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"issue_url":"[^"]*"' | tail -1 | cut -d'"' -f4)
  # `[^"]*` stops at the first `\"` in the serialized state, which silently dropped
  # the rest of the batch. triage_gate now strips `"` from the value before publishing
  # it, so no escape can appear here. This sed stays as defence in depth for a
  # backslash, and the cut leaves room for the rest of the Discord message (2000
  # characters total).
  triage_questions=$(printf '%s' "$triage_questions" | sed 's/[\\"]//g' | cut -c1-800)
fi

# The triage kinds name the issue from issue_url (newest), never from issue_number:
# issue_number is read with head -1 above, which is the first issue a looping run
# triaged.
case "$kind" in
  triage-question|triage-failed)
    if [ -n "$issue_url" ]; then issue="${issue_url##*/}"; fi ;;
esac

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
  # The merge outcome, and the only kind pr-review fires. A rocket, not a checkmark:
  # `complete` already uses ✅ for "opened a PR", and those two events can be one
  # notification apart in a normal chain. report_merged publishes `pr_url` and
  # `issue_number` as context_updates precisely so the enrichment above (unchanged)
  # fills in `$subject` and the PR link on this line.
  merged)   msg="🚀 fabro squash-merged a PR${subject}" ;;
  # The two ways the imported review-merge phase stops without merging. Two kinds and
  # not one, because they ask the operator for different things: `blocked` is the
  # expected outcome of the eligibility gate -- the kill switch, a reviewer verdict,
  # or CI the fix ladder could not land -- and the PR is fine to merge by hand, while
  # `needs-human` means the phase could not produce a verdict at all and the PR now
  # carries the `ai-review-needs-human` label.
  #
  # Neither can carry the reason: `merge_block_reason` is written to a file inside the
  # run's sandbox and never reaches the run context, so the PR comment that the report
  # node posts is where that text lives. `pr_url` IS a context key by this point, so
  # the link block below lands on the PR for both of them.
  blocked)  msg="🟡 fabro finished a review without merging - the PR needs you${subject}" ;;
  needs-human) msg="🔴 fabro's review could not finish - the PR needs you${subject}" ;;
  # A task left the local coder box for a hosted model. Fires once per escalating
  # stage: the rework ladder's hosted tiers, the merge resolver, and the imported
  # CI-fix / rebase ladders. `model` and `node_short` are set above; the run link
  # below carries the rest.
  fallback)  msg="🔼 fabro model fallback: escalated to ${model} (${node_short})${subject}" ;;
  triage-question)
    # \\n, never a literal newline: see the payload line below.
    msg="❓ fabro triage needs a human${subject}\\n${triage_questions}\\nAnswer in a new comment on the issue. The next triage reads it."
    ;;
  triage-failed)
    msg="🟠 fabro issue triage released a claim without finishing${subject}"
    ;;
  *)        msg="ℹ️ fabro run ${run_id} notification ($kind)" ;;
esac

# Links, each added only when we have the pieces for it.
links=""
# The PR is the most useful link when there is one, so it leads.
[ -n "$pr_url" ] && links="${links}\\n${pr_url}"
if [ -n "$base_url" ] && [ "$kind" != "triage-question" ]; then
  links="${links}\\n${base_url}/runs/${run_id}"
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
