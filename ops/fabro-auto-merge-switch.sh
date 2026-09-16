#!/bin/sh
# fabro-auto-merge-switch.sh — flip an auto-merge kill switch.
#
# Usage: fabro-auto-merge-switch.sh <owner/repo | automation-id | host> <on|off>
#
# `host` flips the host-wide switch; anything else flips that one repository's.
# Auto-merge happens only when both read enabled, and neither undoes a merge that has
# already happened.
#
# The per-repo switch is an `auto_merge=true|false` token in the `pr-review-<repo>`
# automation row's `description`, read by fire-pr-review.sh and sent to the run as
# `auto_merge` in args.inputs. It lives there because fabro 0.354.0-nightly.0 has no
# `labels` field on an automation (POST with one returns 422 `unknown field 'labels'`)
# and no `PATCH /automations/{id}` (405); `description` is the only free-form writable
# field on the row, and PUT is a full replacement that requires `If-Match`. This script
# is that GET+PUT, so the incident command is one line and the body is not hand-built.
#
# The host-wide switch is the `FABRO_AUTO_MERGE` *server variable*, injected into the
# run sandbox by pr-review's `[run.environment.env]` as `{{ vars.FABRO_AUTO_MERGE }}`.
# It is a variable and not an entry in ~/fabro/.env because shell-style `${X}` is not
# interpolated in that table — probed 2026-09-16, it arrives as the literal text — and
# fabro's own error for `{{ env.X }}` says to use `{{ vars.X }}` for a non-sensitive
# value. It takes effect on the next run: no .env edit, no restart.
#
# It must EXIST. An unset server variable fails the RunIntent at compile time, so no
# pr-review run is created at all. `off` therefore writes `0`; it never deletes.
#
# Reading the switch (fire-pr-review.sh and ops/provision-server-state.sh use the same
# rule): an absent token is `true` — operator decision 10, auto-merge is the default
# and these switches turn it off — and anything present that is not exactly `true` is
# `false`.
#
# Env:
#   FABRO_API_URL    http://10.10.0.32:32276/api/v1   includes the /api/v1 prefix
#   FABRO_DEV_TOKEN  —                                required; never echoed
#   FABRO_API_TOKEN  —                                accepted as a fallback name
#   DRY_RUN          1                                default; prints the PUT it would send
#
# POSIX sh. No secret belongs in this file: it is tracked in a public repo.
set -u

API_URL="${FABRO_API_URL:-http://10.10.0.32:32276/api/v1}"
TOKEN="${FABRO_DEV_TOKEN:-${FABRO_API_TOKEN:-}}"
DRY_RUN="${DRY_RUN:-1}"

die() { printf '%s\n' "$1" >&2; exit 1; }

[ "$#" -eq 2 ] || die "usage: fabro-auto-merge-switch.sh <owner/repo|automation-id|host> <on|off>"
TARGET_ARG="$1"
WANT_ARG="$2"

case "$WANT_ARG" in
  on) want=true ;;
  off) want=false ;;
  *) die "second argument must be on or off, got '$WANT_ARG'" ;;
esac
case "$DRY_RUN" in 1 | 0) ;; *) die "DRY_RUN must be 0 or 1, got '$DRY_RUN'" ;; esac
[ -n "$TOKEN" ] || die "FABRO_DEV_TOKEN (or FABRO_API_TOKEN) is not set"
for tool in jq curl; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required but not on PATH"
done

tmp="$(mktemp -d)" || die "could not create a temporary directory"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

# ------------------------------------------------------- 0. the host-wide switch ----
#
# POST /variables upserts: it returns 200 and overwrites an existing value rather than
# conflicting. That is exactly what this script wants and exactly what a re-provision
# must not do, which is why ops/provision-server-state.sh creates the variable only
# when it is absent and reports a differing one as drift.
if [ "$TARGET_ARG" = host ]; then
  case "$want" in true) hv=1 ;; *) hv=0 ;; esac
  if [ "$DRY_RUN" = 1 ]; then
    printf 'DRY_RUN=1: nothing will be sent. Re-run with DRY_RUN=0 to flip.\n'
    printf 'would POST %s/variables  {"name":"FABRO_AUTO_MERGE","value":"%s"}\n' "$API_URL" "$hv"
    cur="$(curl -sS -H "Authorization: Bearer $TOKEN" "$API_URL/variables" 2>/dev/null \
      | jq -r '.data[]? | select(.name == "FABRO_AUTO_MERGE") | .value' 2>/dev/null)"
    printf 'current: %s\n' "${cur:-<absent -- every pr-review run will fail to compile>}"
    exit 0
  fi
  code="$(curl -sS -o "$tmp/var.json" -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"name\":\"FABRO_AUTO_MERGE\",\"value\":\"$hv\"}" \
    "$API_URL/variables" 2>/dev/null)" || code=000
  case "$code" in
    200 | 201) ;;
    000) die "could not reach $API_URL to set the host switch" ;;
    401) die "the API rejected the token (HTTP 401)" ;;
    *) die "could not set the host switch (HTTP $code): $(head -c 300 "$tmp/var.json" 2>/dev/null | tr '\n' ' ')" ;;
  esac
  printf 'host switch FABRO_AUTO_MERGE=%s (takes effect on the next run; no restart)\n' "$hv"
  exit 0
fi

# ------------------------------------------------------------ 1. find the row ----
# The list response carries each row's `revision`, which is required in `If-Match` on
# the PUT. Fetch then filter: piping curl into jq takes jq's exit status, so an API
# outage would look like "no such automation".
code="$(curl -sS -o "$tmp/list.json" -w '%{http_code}' \
  -H "Authorization: Bearer $TOKEN" "$API_URL/automations" 2>/dev/null)" || code=000
case "$code" in
  200) ;;
  000) die "could not reach $API_URL to list automations" ;;
  401) die "the API rejected the token (HTTP 401)" ;;
  *) die "could not list automations (HTTP $code): $(head -c 300 "$tmp/list.json" 2>/dev/null | tr '\n' ' ')" ;;
esac

case "$TARGET_ARG" in
  */*)
    jq --arg repo "$TARGET_ARG" \
      '[.data[] | select(.workflow == "pr-review" and .target.repo == $repo)]' \
      "$tmp/list.json" > "$tmp/match.json" \
      || die "could not parse the automations response"
    ;;
  *)
    jq --arg id "$TARGET_ARG" '[.data[] | select(.id == $id)]' \
      "$tmp/list.json" > "$tmp/match.json" \
      || die "could not parse the automations response"
    ;;
esac

match_count="$(jq 'length' "$tmp/match.json")"
case "$match_count" in
  0) die "no automation matches '$TARGET_ARG' (create the rows with ops/provision-server-state.sh)" ;;
  1) ;;
  *) die "$match_count automations match '$TARGET_ARG'; exactly one is required" ;;
esac

id="$(jq -r '.[0].id' "$tmp/match.json")"
rev="$(jq -r '.[0].revision' "$tmp/match.json")"
desc="$(jq -r '.[0].description // ""' "$tmp/match.json")"
old_tok="$(printf '%s' "$desc" | grep -o 'auto_merge=[A-Za-z0-9_-]*' | tail -1 | cut -d= -f2)"

case "$id" in
  pr-review-*) ;;
  *) die "$id is not a pr-review automation; a backlog row has no auto-merge switch" ;;
esac

# --------------------------------------------------------- 2. rewrite the token ----
# Everything else in the description is preserved: it is a human-facing field, and
# stomping the prose to store one token would be a poor trade.
case "$desc" in
  *auto_merge=*) new_desc="$(printf '%s' "$desc" | sed 's/auto_merge=[A-Za-z0-9_-]*/auto_merge='"$want"'/g')" ;;
  '') new_desc="pr-review config-only row (never fired). auto_merge=$want" ;;
  *) new_desc="$desc auto_merge=$want" ;;
esac

# ReplaceAutomationRequest: name, environment_id, target, workflow and triggers are
# required; description and workflow_source ride along so nothing is dropped by the
# full replacement. `environment_id` is sent as-is: the API requires it, and a null one
# is pre-existing broken state this script must not paper over.
jq -c '.[0] | {name, description: $desc, environment_id, target, workflow, triggers}
       + (if (.workflow_source // null) == null then {} else {workflow_source} end)' \
  --arg desc "$new_desc" "$tmp/match.json" > "$tmp/body.json" \
  || die "could not build the replacement body"

printf 'automation   %s\n' "$id"
printf 'description  %s\n' "$desc"
printf 'auto_merge   %s -> %s\n' "${old_tok:-<absent, effectively true>}" "$want"

if [ "$DRY_RUN" = 1 ]; then
  printf 'DRY_RUN=1: nothing sent. Re-run with DRY_RUN=0 to flip it.\n'
  printf '\n== PUT %s/automations/%s ==\n' "$API_URL" "$id"
  printf 'If-Match: "%s"\n' "$rev"
  jq . "$tmp/body.json"
  exit 0
fi

# ------------------------------------------------------------- 3. write and read ----
code="$(curl -sS -o "$tmp/out.json" -w '%{http_code}' -X PUT \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H "If-Match: \"$rev\"" --data-binary "@$tmp/body.json" \
  "$API_URL/automations/$id" 2>/dev/null)" || code=000
case "$code" in
  200) ;;
  000) die "could not reach $API_URL to update $id" ;;
  401) die "the API rejected the token (HTTP 401)" ;;
  412) die "$id changed since it was read (HTTP 412); re-run to pick up the new revision" ;;
  *) die "updating $id failed (HTTP $code): $(head -c 300 "$tmp/out.json" 2>/dev/null | tr '\n' ' ')" ;;
esac

new_tok="$(jq -r '.description // ""' "$tmp/out.json" | grep -o 'auto_merge=[A-Za-z0-9_-]*' | tail -1 | cut -d= -f2)"
printf 'updated      %s reads auto_merge=%s\n' "$id" "$new_tok"
[ "$new_tok" = "$want" ] || die "wrote auto_merge=$want but the server read back '$new_tok'"
printf 'The next fire resolved against %s sees it. Nothing undoes a merge that already happened.\n' "$id"
