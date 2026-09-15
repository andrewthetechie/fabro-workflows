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
# environment_id has drifted is reported, not corrected, because silently
# rewriting live automation state from a bootstrap script is worse than telling
# the operator. No secrets live in this file; the API token and base URL come
# from the environment.
#
# The backlog-* rows were reconstructed to match the pr-review pattern, not read
# back from a live server. Their schedule is created DISABLED. Before trusting
# this script to rebuild a host, diff it against production:
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

if [ -z "$API_URL" ] || [ -z "$TOKEN" ]; then
  echo "Both FABRO_API_URL and FABRO_DEV_TOKEN must be set." >&2
  exit 1
fi

AUTH_HEADER="Authorization: Bearer $TOKEN"

# Set by provision_automation when an existing row does not match this script.
DRIFT_FOUND=0

# provision_automation <id> <environment_id> <repo> <workflow> <schedule_id_or_empty>
#   - workflow is "backlog" or "pr-review"; both live in
#     andrewthetechie/fabro-workflows@main.
#   - schedule_id, when non-empty, adds a DISABLED every-15m schedule row for
#     operator convenience. pr-review gets none: it is fired manually against a
#     named PR, so there is nothing for a cron to poll.
provision_automation() {
  id="$1"
  env_id="$2"
  repo="$3"
  workflow="$4"
  schedule_id="$5"

  # Fetch first, then filter. Piping curl straight into jq takes jq's exit
  # status, so an API outage would come back as empty output and be
  # indistinguishable from "this automation does not exist yet" — the script
  # would then POST and report a misleading 409.
  if ! curl -fsS -m 15 -H "$AUTH_HEADER" "$API_URL/automations" > /tmp/provision_list.json; then
    echo "FAILED to list automations; cannot tell whether $id exists." >&2
    return 1
  fi

  existing_env=$(jq -r --arg id "$id" \
    '.data[]? | select(.id == $id) | .environment_id // ""' /tmp/provision_list.json)
  if [ -n "$existing_env" ]; then
    if [ "$existing_env" = "$env_id" ]; then
      echo "exists: $id (env=$existing_env)"
    else
      echo "DRIFT: $id has environment_id=$existing_env, expected $env_id. Not corrected." >&2
      DRIFT_FOUND=1
    fi
    return 0
  fi

  if [ -n "$schedule_id" ]; then
    schedule_json=",{\"type\":\"schedule\",\"id\":\"$schedule_id\",\"enabled\":false,\"expression\":\"*/15 * * * *\"}"
  else
    schedule_json=""
  fi

  payload="{\"id\":\"$id\",\"name\":\"$id\",\"environment_id\":\"$env_id\",\"target\":{\"kind\":\"git\",\"repo\":\"$repo\",\"branch\":\"main\"},\"workflow\":\"$workflow\",\"workflow_source\":{\"repo\":\"andrewthetechie/fabro-workflows\",\"branch\":\"main\"},\"triggers\":[{\"type\":\"api\",\"id\":\"manual\",\"enabled\":true}$schedule_json]}"

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

echo "Provisioning pr-review automations..."
provision_automation pr-review-jelly-swipe python andrewthetechie/jelly-swipe pr-review ""
provision_automation pr-review-lawncare-saas python-node andrewthetechie/lawncare-saas pr-review ""
provision_automation pr-review-womens-fantasy-sports ts andrewthetechie/womens-fantasy-sports pr-review ""
provision_automation pr-review-writers-app rust-node andrewthetechie/writers-app pr-review ""

echo "Provisioning backlog automations..."
provision_automation backlog-jelly-swipe python andrewthetechie/jelly-swipe backlog every-15m
provision_automation backlog-lawncare-saas python-node andrewthetechie/lawncare-saas backlog every-15m
provision_automation backlog-womens-fantasy-sports ts andrewthetechie/womens-fantasy-sports backlog every-15m
provision_automation backlog-writers-app rust-node andrewthetechie/writers-app backlog every-15m

if [ "$DRIFT_FOUND" -ne 0 ]; then
  echo "Done, with drift reported above. Reconcile those automations by hand." >&2
  exit 2
fi

echo "Done. Backlog schedules are created disabled; enable them if this was a rebuild."
