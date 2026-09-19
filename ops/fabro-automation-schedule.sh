#!/bin/sh
# fabro-automation-schedule.sh — turn one automation's schedule triggers on or off.
#
# Usage: fabro-automation-schedule.sh <automation-id> <on|off>
#
# The `<automation-id>` is the full row id (`backlog-jelly-swipe`), never an
# `owner/repo`: this script is about one named row, and a repo can carry three of them
# (`backlog`, `pr-review`, `issue-triage`) with different schedules.
#
# The cutover it exists for (docs/scheduler/13-turn-off-backlog-automations.md): the
# four `backlog-<repo>` rows are the *other* admission controller. Once their schedules
# are off, the coder scheduler is the only thing that starts a `backlog` run, so a cron
# and the scheduler cannot both push work onto the same two single-slot boxes (ADR
# 0005). The rows are kept, not deleted: `provision-server-state.sh` still reads their
# `environment_id` for drift, and `fabro-fire-backlog.sh` still looks up the image there.
#
# fabro has no `PATCH /automations/{id}` (405), so this is GET + full-body PUT with
# `If-Match` on the row's `revision`. PUT is a **full replacement**: the body is rebuilt
# from the row that was just read. The `jq` projection below is load-bearing — a PUT
# that omits `workflow_source` when the row has one silently unpins the workflow source,
# which would send every later fire at whatever the default is instead of
# `andrewthetechie/fabro-workflows@main`.
#
# Only `schedule` triggers are touched. An `api` trigger's `enabled` is left exactly as
# it was read: `POST /automations/{id}/runs` answers `409 automation_api_trigger_disabled`
# when no **enabled** API trigger remains, so silencing `api:manual` as a side effect
# would take the manual fire path down with the schedule.
#
# Idempotent: a row whose schedule triggers already read the wanted value is reported
# and not PUT. A `412` (`If-Match` mismatch) is fatal and loud — something else
# changed the row between the read and the write, and retrying with a freshly read
# revision would silently overwrite that change.
#
# Re-enabling a schedule is this script with `on`. There is no other undo.
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

[ "$#" -eq 2 ] || die "usage: fabro-automation-schedule.sh <automation-id> <on|off>"
id="$1"
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

jq --arg id "$id" '[.data[] | select(.id == $id)]' "$tmp/list.json" > "$tmp/match.json" \
  || die "could not parse the automations response"

case "$(jq 'length' "$tmp/match.json")" in
  0) die "no automation with id '$id' (create the rows with ops/provision-server-state.sh)" ;;
  1) ;;
  *) die "more than one automation has id '$id'; that should be impossible" ;;
esac

rev="$(jq -r '.[0].revision' "$tmp/match.json")"
sched_total="$(jq -r '.[0] | [.triggers[] | select(.type == "schedule")] | length' "$tmp/match.json")"
sched_state="$(jq -r '.[0] | [.triggers[] | select(.type == "schedule")
                         | "\(.id)=\(.enabled)"] | join(", ")' "$tmp/match.json")"
api_state="$(jq -r '.[0] | [.triggers[] | select(.type == "api")
                         | "\(.id)=\(.enabled)"] | join(", ")' "$tmp/match.json")"

# A row with no schedule trigger — every `pr-review-<repo>` — has nothing to turn on
# or off. Saying "already off" for an empty set would be technically true and useless,
# and the operator would read it as "this row is silenced" rather than "this row was
# never scheduled".
[ "$sched_total" -gt 0 ] ||
  die "$id has no schedule trigger; there is nothing to turn $WANT_ARG (api triggers: ${api_state:-none})"

printf 'automation   %s\n' "$id"
printf 'schedules    %s\n' "$sched_state"
printf 'api triggers %s (left alone)\n' "${api_state:-<none>}"

if [ "$(jq -r --argjson want "$want" \
  '.[0] | [.triggers[] | select(.type == "schedule") | (.enabled == $want)] | all' \
  "$tmp/match.json")" = true ]; then
  printf 'already %s: every schedule trigger reads enabled=%s. Nothing sent.\n' "$WANT_ARG" "$want"
  exit 0
fi

# ------------------------------------------------------- 2. build the body ----
# ReplaceAutomationRequest: name, environment_id, target, workflow and triggers are
# required; description and workflow_source ride along so the full replacement drops
# nothing. `description` is sent as read, including a `null` — the field is nullable,
# and inventing prose for a row that has none would make every re-run a diff.
jq -c --argjson want "$want" '
    .[0]
    | .triggers |= map(if .type == "schedule" then .enabled = $want else . end)
    | {name, description, environment_id, target, workflow, triggers}
      + (if (.workflow_source // null) == null then {} else {workflow_source} end)' \
  "$tmp/match.json" > "$tmp/body.json" \
  || die "could not build the replacement body"

printf 'schedules -> %s\n' "$want"

if [ "$DRY_RUN" = 1 ]; then
  printf 'DRY_RUN=1: nothing sent. Re-run with DRY_RUN=0 to turn the schedules %s.\n' "$WANT_ARG"
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
  412) die "$id changed since it was read (HTTP 412). Something else edited the row; re-read it and decide what to do — do not blindly retry against a fresh revision" ;;
  *) die "updating $id failed (HTTP $code): $(head -c 300 "$tmp/out.json" 2>/dev/null | tr '\n' ' ')" ;;
esac

[ "$(jq -r --argjson want "$want" \
  '[.triggers[] | select(.type == "schedule") | (.enabled == $want)] | all' \
  "$tmp/out.json")" = true ] ||
  die "wrote schedule enabled=$want but the server read back something else"

# The one field a full replacement can silently lose, so it is asserted rather than
# only printed. Only when the row had one: a row with no `workflow_source` is a row
# that already resolves against the default, and inventing one here would be a change
# this script has no business making.
if [ "$(jq -r '(.[0].workflow_source // null) == null' "$tmp/match.json")" = false ]; then
  [ "$(jq -r '(.workflow_source // null) != null' "$tmp/out.json")" = true ] ||
    die "the PUT dropped workflow_source from $id; re-pin it by hand before anything fires"
fi

printf 'updated      %s now reads %s\n' "$id" \
  "$(jq -r '[.triggers[] | select(.type == "schedule") | "\(.id)=\(.enabled)"] | join(", ")' "$tmp/out.json")"
printf 'api triggers %s (unchanged); workflow_source %s@%s\n' \
  "$(jq -r '[.triggers[] | select(.type == "api") | "\(.id)=\(.enabled)"] | join(", ")' "$tmp/out.json")" \
  "$(jq -r '.workflow_source.repo // "<absent>"' "$tmp/out.json")" \
  "$(jq -r '.workflow_source.branch // "-"' "$tmp/out.json")"
