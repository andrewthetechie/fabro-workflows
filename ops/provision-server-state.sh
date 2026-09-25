#!/bin/sh
# Provision the server-side automation state a freshly rebuilt fabro host needs:
# one automation per in-scope target repo for both the backlog and pr-review
# workflows. Each repo runs in an image that can build that repo
# (buildpack-deps:noble has no cargo / bun and would fail CI on two of the four
# repos), which is why there is one automation per repo with a specific
# environment_id.
#
# Idempotent: an automation that already exists is never recreated, so re-running
# after a partial failure is safe. It is NOT a reconciler — a row whose
# environment_id or trigger set has drifted is reported, not corrected, because
# silently rewriting live automation state from a bootstrap script is worse than
# telling the operator. No secrets live in this file; the API token and base URL come
# from the environment.
#
# The backlog-* rows were reconstructed to match the pr-review pattern, not read
# back from a live server. Their schedule is created DISABLED. Before trusting
# this script to rebuild a host, diff it against production:
#
# The pr-review-* rows also carry the per-repo auto-merge kill switch: an
# `auto_merge=true|false` token in the row's `description`. That is not a stylistic
# choice — fabro 0.354.0-nightly.0 has no `labels` field on an automation (POST with
# one returns 422 `unknown field 'labels'`) and no `PATCH /automations/{id}` (405),
# and `description` is the only free-form writable field on the row. Flip it with
# `ops/fabro-auto-merge-switch.sh <repo> on|off`; flip it by hand and this script
# reports the row as drift rather than putting it back.
#
#   curl -sS -H "Authorization: Bearer $FABRO_DEV_TOKEN" "$FABRO_API_URL/automations" \
#     | jq -r '.data[] | "\(.id)\t\(.environment_id)\t\([.triggers[]|"\(.type):\(.id):\(.enabled)"]|join(","))"'
#
# If production's backlog schedules are enabled, a host rebuilt from this script
# comes back with backlog not running.
#
# Usage:
#   FABRO_API_URL=http://<host>:32276/api/v1 \
#   FABRO_DEV_TOKEN=<token> \
#   ./ops/provision-server-state.sh
#
# Environment ids (python, python-node, ts, rust-node) must already be
# registered on the server. Preflight with `GET /api/v1/environments` if in
# doubt.

set -eu

API_URL="${FABRO_API_URL:-}"
TOKEN="${FABRO_DEV_TOKEN:-}"
DRY_RUN="${DRY_RUN:-0}"

if [ -z "$API_URL" ] || [ -z "$TOKEN" ]; then
  echo "Both FABRO_API_URL and FABRO_DEV_TOKEN must be set." >&2
  exit 1
fi

AUTH_HEADER="Authorization: Bearer $TOKEN"

# Set by provision_automation when an existing row does not match this script.
DRIFT_FOUND=0
# Incremented when a row could not be created. Rows are provisioned with
# `|| FAILED=...` so one bad row does not abort the rest and skip the summary.
FAILED=0

# Read the automation list once, up front. Fetch first, then filter: piping
# curl straight into jq takes jq's exit status, so an API outage would come
# back as empty output and be indistinguishable from "nothing exists yet" —
# the script would then POST every row and report misleading 409s. Nothing
# below re-reads it: each id is provisioned at most once, so a row this script
# creates is never looked up again.
if ! curl -fsS -m 15 -H "$AUTH_HEADER" "$API_URL/automations" > /tmp/provision_list.json; then
  echo "FAILED to list automations from $API_URL; cannot tell what already exists." >&2
  exit 1
fi

# check_triggers <id> <schedule_id_or_empty> [schedule_expr]
#
# An existing row's triggers are compared as well as its environment_id. This is
# not a theoretical case: backlog-writers-app sat in production carrying only its
# disabled schedule and no api:manual row, so it could not be fired through the
# API at all, and nothing reported it — environment_id matched, and that was the
# only thing this script looked at. Corrected by hand on 2026-09-16; this check
# is what would have caught it.
#
# The schedule's `enabled` flag is deliberately NOT compared. Schedules are
# created disabled and turned on by the operator, so comparing it would report
# drift on every correctly-running host. (The arch-review rows are created
# ENABLED, ADR 0012, and their flag stays the operator's too.) The api trigger's
# `enabled` IS compared:
# a disabled one is exactly as dead as a missing one. The schedule's `expression`
# IS compared: the staggering offsets are this script's state to guard.
check_triggers() {
  t_id="$1"
  t_schedule="$2"
  t_expr="${3:-}"

  t_want="api:manual"
  if [ -n "$t_schedule" ]; then
    t_want="$t_want schedule:$t_schedule"
  fi

  t_got=$(jq -r --arg id "$t_id" \
    '.data[]? | select(.id == $id) | .triggers[]? | "\(.type):\(.id)"' /tmp/provision_list.json)

  for t_w in $t_want; do
    if ! printf '%s\n' "$t_got" | grep -qx "$t_w"; then
      echo "DRIFT: $t_id is missing the $t_w trigger. Not corrected." >&2
      DRIFT_FOUND=1
    fi
  done

  for t_g in $t_got; do
    case " $t_want " in
      *" $t_g "*) ;;
      *)
        echo "DRIFT: $t_id has an unexpected $t_g trigger. Not corrected." >&2
        DRIFT_FOUND=1
        ;;
    esac
  done

  t_api_enabled=$(jq -r --arg id "$t_id" \
    '.data[]? | select(.id == $id) | .triggers[]?
     | select(.type == "api" and .id == "manual") | .enabled' /tmp/provision_list.json)
  if [ -n "$t_api_enabled" ] && [ "$t_api_enabled" != "true" ]; then
    echo "DRIFT: $t_id has api:manual disabled, so it cannot be fired through the API. Not corrected." >&2
    DRIFT_FOUND=1
  fi

  if [ -n "$t_schedule" ] && [ -n "$t_expr" ]; then
    t_got_expr=$(jq -r --arg id "$t_id" --arg sid "$t_schedule" \
      '.data[]? | select(.id == $id) | .triggers[]?
       | select(.type == "schedule" and .id == $sid) | .expression // ""' /tmp/provision_list.json)
    if [ -n "$t_got_expr" ] && [ "$t_got_expr" != "$t_expr" ]; then
      echo "DRIFT: $t_id schedule $t_schedule has expression '$t_got_expr', expected '$t_expr'. Not corrected." >&2
      DRIFT_FOUND=1
    fi
  fi
}

# description_auto_merge <description>
#
# The one parser for the per-repo switch, and the behaviour it defines. Three outcomes,
# not two: no mention of the token at all is `true` (operator decision 10 — auto-merge
# is the default and the switches turn it off); an explicit `auto_merge=true` is `true`;
# and anything else that mentions it — malformed, empty, `0`, `TRUE` — is `false`. A
# token that does not parse is a switch nobody can trust, so it fails closed rather
# than falling back to the default. fire-pr-review.sh parses the same token the same
# way; keep the two in step, because a re-provision that disagrees with the fire path is
# the one way this switch can be wrong at the worst moment.
#
description_auto_merge() {
  d_desc="${1:-}"
  d_am=true
  case "$d_desc" in
    *auto_merge*)
      d_token=$(printf '%s' "$d_desc" | grep -o 'auto_merge=[A-Za-z0-9_-]*' | tail -1 | cut -d= -f2)
      case "$d_token" in
        true) d_am=true ;;
        *) d_am=false ;;
      esac
      ;;
  esac
  printf '%s\n' "$d_am"
}

# provision_variable <name> <default_value>
#
# Server variables, which pr-review's [run.environment.env] reads as
# `{{ vars.FABRO_AUTO_MERGE }}`. This is not optional plumbing: an unset variable fails
# the RunIntent at compile time ("Run config variable interpolation failed"), so a host
# missing FABRO_AUTO_MERGE creates no pr-review run at all.
#
# Create-if-absent, never overwrite. `POST /variables` upserts -- it returns 200 and
# replaces an existing value -- so a naive POST here would silently re-arm a switch an
# operator flipped off during an incident. That is the same rule the automation rows
# follow, and it is the one property this script has to keep: a re-provision must never
# undo a deliberate kill.
provision_variable() {
  v_name="$1"
  v_default="$2"

  v_cur=$(curl -fsS -m 15 -H "$AUTH_HEADER" "$API_URL/variables" 2>/dev/null \
    | jq -r --arg n "$v_name" '.data[]? | select(.name == $n) | .value' 2>/dev/null)

  if [ -n "$v_cur" ]; then
    if [ "$v_cur" = "$v_default" ]; then
      echo "OK: variable $v_name is $v_cur"
    else
      echo "DRIFT: variable $v_name is '$v_cur', not the provisioned '$v_default'. Not corrected." >&2
      DRIFT_FOUND=1
    fi
    return 0
  fi

  if [ "$DRY_RUN" = "1" ]; then
    echo "would create: variable $v_name = $v_default"
    return 0
  fi

  v_code=$(curl -sS -m 15 -o /tmp/provision_var.json -w '%{http_code}' -X POST \
    -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg n "$v_name" --arg v "$v_default" '{name:$n, value:$v}')" \
    "$API_URL/variables" 2>/dev/null) || v_code=000
  case "$v_code" in
    200 | 201) echo "CREATED: variable $v_name = $v_default" ;;
    *) echo "FAILED: could not create variable $v_name (HTTP $v_code)" >&2; return 1 ;;
  esac
}

# provision_automation <id> <environment_id> <repo> <workflow> <schedule_id_or_empty> [schedule_expr] [auto_merge] [schedule_enabled]
#   - workflow is "backlog", "pr-review", "issue-triage" or "arch-review"; all live in
#     andrewthetechie/fabro-workflows@main.
#   - schedule_id, when non-empty, adds a DISABLED schedule row for operator
#     convenience, using schedule_expr. The four backlog schedules are staggered
#     three minutes apart (ADR 0001) so all four never fire in the same minute.
#     pr-review gets none: it is fired manually against a named PR, so there is
#     nothing for a cron to poll.
#   - schedule_enabled defaults to false. Only the arch-review rows pass true (ADR
#     0012 D7): theirs are the one set of schedules this deployment runs on. Like
#     every other schedule flag it is set only when the row is created; an existing
#     row's flag is the operator's and is never compared or rewritten.
#   - auto_merge defaults to true and is written into a pr-review row's description
#     only. It is the provisioned value of decision 10, which is why the default is
#     `true` here and why an existing row that disagrees is reported, never rewritten:
#     a switch flipped off by hand during an incident must survive a re-provision.
provision_automation() {
  id="$1"
  env_id="$2"
  repo="$3"
  workflow="$4"
  schedule_id="$5"
  schedule_expr="${6:-}"
  auto_merge="${7:-true}"
  schedule_enabled="${8:-false}"

  case "$auto_merge" in
    true | false) ;;
    *) echo "FAILED: $id auto_merge must be true or false, got '$auto_merge'" >&2; return 1 ;;
  esac
  case "$schedule_enabled" in
    true | false) ;;
    *) echo "FAILED: $id schedule_enabled must be true or false, got '$schedule_enabled'" >&2; return 1 ;;
  esac

  existing_env=$(jq -r --arg id "$id" \
    '.data[]? | select(.id == $id) | .environment_id // ""' /tmp/provision_list.json)
  if [ -n "$existing_env" ]; then
    if [ "$existing_env" = "$env_id" ]; then
      echo "exists: $id (env=$existing_env)"
    else
      echo "DRIFT: $id has environment_id=$existing_env, expected $env_id. Not corrected." >&2
      DRIFT_FOUND=1
    fi
    check_triggers "$id" "$schedule_id" "$schedule_expr"
    if [ "$workflow" = "pr-review" ]; then
      existing_desc=$(jq -r --arg id "$id" \
        '.data[]? | select(.id == $id) | .description // ""' /tmp/provision_list.json)
      # Passed as an argument, not piped: description_auto_merge reads "$1", and a
      # pipe would hand it nothing and silently answer `true` for every row.
      existing_am=$(description_auto_merge "$existing_desc")
      if [ "$existing_am" != "$auto_merge" ]; then
        echo "DRIFT: $id has auto_merge=$existing_am, expected $auto_merge. Not corrected: a switch flipped by hand must survive a re-provision." >&2
        DRIFT_FOUND=1
      fi
    fi
    return 0
  fi

  if [ -n "$schedule_id" ]; then
    # jq builds the object; the separator is added here, in the shell. The jq program
    # used to begin with a literal `,` so it could be spliced into the payload below,
    # which is a jq syntax error: `jq -nc ',{...}'` prints nothing and exits 3. Under
    # `set -e` that aborted the whole script, so creating a *new* backlog automation
    # had been impossible since the schedules were staggered — invisible on the live
    # host, where those rows already exist and take the drift path.
    schedule_json=",$(jq -nc --arg sid "$schedule_id" --arg expr "$schedule_expr" --argjson en "$schedule_enabled" \
      '{type:"schedule",id:$sid,enabled:$en,expression:$expr}')"
  else
    schedule_json=""
  fi

  # The per-repo auto-merge switch. pr-review rows only; a backlog row has nothing
  # that reads it. `$auto_merge` is narrowed to true|false above, so it cannot carry
  # JSON syntax into this string.
  if [ "$workflow" = "pr-review" ]; then
    description_field="\"description\":\"pr-review config-only row for $repo (never fired). auto_merge=$auto_merge\","
  else
    description_field=""
  fi

  payload="{\"id\":\"$id\",\"name\":\"$id\",$description_field\"environment_id\":\"$env_id\",\"target\":{\"kind\":\"git\",\"repo\":\"$repo\",\"branch\":\"main\"},\"workflow\":\"$workflow\",\"workflow_source\":{\"repo\":\"andrewthetechie/fabro-workflows\",\"branch\":\"main\"},\"triggers\":[{\"type\":\"api\",\"id\":\"manual\",\"enabled\":true}$schedule_json]}"

  if [ "$DRY_RUN" = "1" ]; then
    echo "would create: $id (env=$env_id)"
    return 0
  fi

  http_code=$(curl -sS -m 15 -o /tmp/provision_out.json -w '%{http_code}' \
    -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
    -d "$payload" "$API_URL/automations")

  if [ "$http_code" = "201" ]; then
    echo "created: $id (env=$env_id)"
  else
    echo "FAILED to create $id: HTTP $http_code $(cat /tmp/provision_out.json 2>/dev/null)" >&2
    return 1
  fi
}

# The host-wide half of the auto-merge kill switch. Provisioned to decision 10's
# default (on); flip it with `ops/fabro-auto-merge-switch.sh host off`, which takes
# effect on the next run with no restart. Created before the automations because a
# pr-review run cannot compile without it. FABRO_API_URL is the base URL `backlog`
# hands to `fire-pr-review.sh` through `[run.environment.env]`.
#
# `issue-triage` used to read it too, in a capacity gate that called /system/info at
# startup; that stage was deleted 2026-09-18, so `backlog` is now the only consumer
# and this variable fails a backlog run closed if it is missing
# (`docs/scheduler/03-triage-capacity-and-cap.md`).
echo "Provisioning server variables..."
provision_variable FABRO_AUTO_MERGE 1 || FAILED=$((FAILED+1))
provision_variable FABRO_API_URL "http://10.10.0.32:32276/api/v1" || FAILED=$((FAILED+1))

echo "Provisioning pr-review automations..."
# The trailing `true` is the per-repo auto-merge switch, named explicitly: it is the
# value decision 10 says is the default, and a row that is created carrying it is a
# row an operator can flip without a deploy.
provision_automation pr-review-jelly-swipe python andrewthetechie/jelly-swipe pr-review "" "" true || FAILED=$((FAILED+1))
provision_automation pr-review-lawncare-saas python-node andrewthetechie/lawncare-saas pr-review "" "" true || FAILED=$((FAILED+1))
provision_automation pr-review-womens-fantasy-sports ts andrewthetechie/womens-fantasy-sports pr-review "" "" true || FAILED=$((FAILED+1))
provision_automation pr-review-writers-app rust-node andrewthetechie/writers-app pr-review "" "" true || FAILED=$((FAILED+1))

echo "Provisioning backlog automations..."
provision_automation backlog-jelly-swipe python andrewthetechie/jelly-swipe backlog every-15m "2-59/15 * * * *" || FAILED=$((FAILED+1))
provision_automation backlog-lawncare-saas python-node andrewthetechie/lawncare-saas backlog every-15m "5-59/15 * * * *" || FAILED=$((FAILED+1))
provision_automation backlog-womens-fantasy-sports ts andrewthetechie/womens-fantasy-sports backlog every-15m "8-59/15 * * * *" || FAILED=$((FAILED+1))
provision_automation backlog-writers-app rust-node andrewthetechie/writers-app backlog every-15m "11-59/15 * * * *" || FAILED=$((FAILED+1))

echo "Provisioning issue-triage automations..."
provision_automation issue-triage-jelly-swipe python andrewthetechie/jelly-swipe issue-triage hourly "30 * * * *" || FAILED=$((FAILED+1))
provision_automation issue-triage-lawncare-saas python-node andrewthetechie/lawncare-saas issue-triage hourly "35 * * * *" || FAILED=$((FAILED+1))
provision_automation issue-triage-womens-fantasy-sports ts andrewthetechie/womens-fantasy-sports issue-triage hourly "40 * * * *" || FAILED=$((FAILED+1))
provision_automation issue-triage-writers-app rust-node andrewthetechie/writers-app issue-triage hourly "45 * * * *" || FAILED=$((FAILED+1))

# arch-review (ADR 0012 D7): created with the schedule ENABLED, the only rows that are.
# Two fixed weekdays per repo, 3 or 4 days apart, and never two repos in the same hour
# of the same day. The `true` before it is auto_merge, which only pr-review rows read.
echo "Provisioning arch-review automations..."
provision_automation arch-review-jelly-swipe python andrewthetechie/jelly-swipe arch-review twice-weekly "0 4 * * 1,4" true true || FAILED=$((FAILED+1))
provision_automation arch-review-lawncare-saas python-node andrewthetechie/lawncare-saas arch-review twice-weekly "0 4 * * 2,5" true true || FAILED=$((FAILED+1))
provision_automation arch-review-womens-fantasy-sports ts andrewthetechie/womens-fantasy-sports arch-review twice-weekly "0 4 * * 3,6" true true || FAILED=$((FAILED+1))
provision_automation arch-review-writers-app rust-node andrewthetechie/writers-app arch-review twice-weekly "0 5 * * 0,3" true true || FAILED=$((FAILED+1))

if [ "$FAILED" -ne 0 ]; then
  echo "$FAILED automation(s) could not be created; see the errors above." >&2
  exit 1
fi

if [ "$DRIFT_FOUND" -ne 0 ]; then
  echo "Done, with drift reported above. Reconcile those automations by hand." >&2
  exit 2
fi

echo "Done. Backlog and issue-triage schedules are created disabled; enable them if this was a rebuild. arch-review schedules are created enabled (ADR 0012)."
