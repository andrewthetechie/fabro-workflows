#!/bin/sh
# Provision the per-box coder model groups that the coder scheduler pins to.
#
# The scheduler leases one coder box for a whole run, and that choice has to be
# legible to LiteLLM because a fabro run can only name a provider *id* and the
# provider's `base_url` is server-global settings (docs/scheduler/
# 00-overview-and-contracts.md, finding 3). The lever that works is the model
# name in the run's stylesheet, so each box gets its own model group here while
# `coders` keeps load-balancing both:
#
#   coders    -> 10.10.0.29:8000 (existing, untouched)
#   coders    -> 10.10.0.56:8000 (existing, untouched)
#   coders-a  -> 10.10.0.29:8000 (this script)
#   coders-b  -> 10.10.0.56:8000 (this script)
#
# This creates two *new* rows and writes nothing else. The existing `coders`
# rows are read and reported, never created or updated: they are the group every
# unpinned run already calls, and POST /model/update replaces `litellm_params`
# rather than merging, so a malformed body there drops `api_base` for the whole
# pool. Keeping `coders` intact is also what makes this change invisible to the
# runs in flight while it is applied.
#
# Idempotent: rows are listed once up front and only a missing one is created,
# so a re-run after a partial failure is safe. It is NOT a reconciler — a row
# that exists with a different `api_base` is reported, not corrected, because a
# `coders-a` silently pointing at the other box is worse than one that is absent.
#
# The virtual key matters as much as the rows. A LiteLLM key carries its own
# model allowlist, independent of which models exist, and a key that cannot see
# `coders-a` gets `403 key not allowed to access model` on every pinned turn —
# not an empty response, and not a fallback. Verified live 2026-09-18: fabro's
# key rejected `deepseek`, a model that exists, with exactly that error. So this
# script also adds both new names to `LITELLM_FABRO_KEY`'s allowlist. That is
# the same trap `provision-litellm-models.sh` documents for `high-reasoning`,
# which cost a live issue-triage fire on 2026-09-16.
#
# `api_key` is also mandatory, and it is the one field `GET /v1/model/info`
# cannot show you. These boxes are plain-http llama.cpp with no auth, so the
# value is only there to satisfy the OpenAI SDK, which refuses to build a client
# without one — but a row created from the `/v1/model/info` view alone has no
# `api_key` and fails every call with `500 litellm.AuthenticationError: ...
# The api_key client option must be set`. That is not hypothetical: the first
# version of this script copied the nine visible fields, and both new groups
# answered 500 until the field was added. The existing `coders` rows carry the
# same kind of 5-character placeholder, readable only from the database
# (`LiteLLM_ProxyModelTable.litellm_params->>'api_key'`, encrypted at rest).
# Any non-empty value works: both boxes ignore `Authorization` — verified
# 2026-09-18 with `Bearer sk-bogus-probe`, which returned 200 from each.
#
# Usage:
#   LITELLM_URL=https://litellm.herrington.services \
#   LITELLM_MASTER_KEY=<master key> \
#   LITELLM_FABRO_KEY=<fabro's LiteLLM virtual key> \
#   ./ops/provision-coder-groups.sh
#
# The **master** key, not FABRO_LITELLM_KEY: the latter is an `internal_user`
# and `POST /model/new` answers 403 for it. Read the master key out of the pod:
#   kubectl -n litellm exec deploy/litellm -- printenv PROXY_MASTER_KEY
#
# Environment:
#   DRY_RUN: if 1 (the default), print each payload and exit without creating.
#   VERIFY: if 1 (the default), send one 1-token completion to every row this
#     run *created*, with the master key, and fail on a non-2xx. A row that
#     exists is not a row that answers — the `api_key` failure above is
#     invisible to `/v1/model/info`, so nothing short of calling it catches a
#     broken creation. Skipped on a re-run that creates nothing, so an
#     idempotent pass stays free of side effects.
#   LITELLM_URL: base URL of the LiteLLM proxy admin API (required)
#   LITELLM_MASTER_KEY: master key (required, never echoed, never written)
#   LITELLM_FABRO_KEY: the virtual key fabro's workflows call LiteLLM with.
#     When set, `coders-a` and `coders-b` are added to its model allowlist if
#     missing; when unset the script says SKIPPED and still exits 0, because
#     the rows themselves are useful without it.
#
# Verify afterwards — the row table:
#   curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
#     "$LITELLM_URL/v1/model/info" \
#   | jq -r '.data[] | select(.model_name|startswith("coders")) |
#            "\(.model_name)\t\(.litellm_params.api_base)"' | sort
#
# and that each name actually serves, which is the only check that catches a
# row that exists but cannot be called (the master key works here too; the fabro
# key additionally proves the allowlist):
#   for m in coders-a coders-b; do curl -sS -o /dev/null -w "$m %{http_code}\n" \
#     -X POST -H "Authorization: Bearer $LITELLM_FABRO_KEY" \
#     -H 'Content-Type: application/json' \
#     -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":1}" \
#     "$LITELLM_URL/v1/chat/completions"; done

set -eu

DRY_RUN="${DRY_RUN:-1}"
VERIFY="${VERIFY:-1}"
API_URL="${LITELLM_URL:-}"
MASTER_KEY="${LITELLM_MASTER_KEY:-}"
FABRO_KEY="${LITELLM_FABRO_KEY:-}"

if [ -z "$API_URL" ] || [ -z "$MASTER_KEY" ]; then
  echo "Both LITELLM_URL and LITELLM_MASTER_KEY must be set." >&2
  exit 1
fi

AUTH_HEADER="Authorization: Bearer $MASTER_KEY"

# The two names this script owns, referenced by the key-allowlist step too.
CODER_A="coders-a"
CODER_B="coders-b"

# Set when live state does not match what this script expects.
DRIFT_FOUND=0
# Incremented when a row could not be created or a key could not be updated.
# The helpers below report failure by return code only; every increment happens
# at a call site, so one failure is counted once.
FAILED=0
# Rows created by *this* invocation, space-separated. Only these are smoke
# tested, so a re-run that creates nothing issues no completion at all.
CREATED_MODELS=""

# One private work directory, removed on every exit path. Fixed /tmp names would
# be predictable and world-writable: `curl -o` follows a symlink, so a stale or
# planted file at a known path is both a way to poison what this script reads back
# and a way to make it write somewhere it should not.
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/provision-coder-groups.XXXXXX")
trap 'rm -rf "$WORK_DIR"' EXIT HUP INT TERM

MODELS_JSON="$WORK_DIR/models.json"

# Read the model list once, up front. Fetch first, then filter: piping curl
# straight into jq takes jq's exit status, so a gateway outage would look like
# an empty list and the script would then POST every row and report misleading
# errors on top of the real one.
if ! curl -fsS -m 15 -H "$AUTH_HEADER" "$API_URL/v1/model/info" > "$MODELS_JSON" 2>/dev/null; then
  echo "FAILED to list models from $API_URL; cannot tell what already exists." >&2
  exit 1
fi

# describe_failure <http_code> <body_file>
#
# One line, no secret material, and a named cause for the 403 that a wrong key
# produces — that error body talks about teams and PROXY_ADMIN, and the operator
# only needs the second half.
describe_failure() {
  d_http="$1"
  d_body="$2"
  if [ "$d_http" = "403" ]; then
    echo "HTTP 403 — this key is not PROXY_ADMIN, so it cannot change models. Use the master key."
    return 0
  fi
  printf 'HTTP %s %s' "$d_http" "$(head -c 400 "$d_body" 2>/dev/null || true)"
}

# provision_coder_group <model_name> <litellm_params_json> <description>
#
# Create a per-box model group if a row with that name and `api_base` does not
# already exist. Report drift and change nothing when the name is taken by a row
# pointing somewhere else.
provision_coder_group() {
  c_name="$1"
  c_params="$2"
  c_desc="$3"
  c_base=$(printf '%s' "$c_params" | jq -r '.api_base')

  c_match=$(jq -r --arg n "$c_name" --arg b "$c_base" \
    '[.data[]? | select(.model_name == $n and .litellm_params.api_base == $b)] | length' \
    "$MODELS_JSON")

  if [ "$c_match" -gt 0 ]; then
    echo "already present: $c_name -> $c_base"
    return 0
  fi

  c_other=$(jq -r --arg n "$c_name" --arg b "$c_base" \
    '[.data[]? | select(.model_name == $n and .litellm_params.api_base != $b)
      | .litellm_params.api_base] | join(", ")' \
    "$MODELS_JSON")

  if [ -n "$c_other" ]; then
    echo "DRIFT: $c_name already exists pointing at [$c_other], expected $c_base. Not corrected." >&2
    DRIFT_FOUND=1
    return 0
  fi

  c_payload=$(jq -nc --arg name "$c_name" --argjson params "$c_params" --arg desc "$c_desc" \
    '{model_name: $name, litellm_params: $params, model_info: {description: $desc}}')

  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN: would create $c_name with payload:"
    printf '%s\n' "$c_payload" | jq .
    return 0
  fi

  c_http=$(curl -sS -m 15 -o "$WORK_DIR/model-new.json" -w '%{http_code}' \
    -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
    -d "$c_payload" "$API_URL/model/new" 2>/dev/null) || c_http=000

  case "$c_http" in
    200 | 201)
      echo "created: $c_name -> $c_base"
      CREATED_MODELS="$CREATED_MODELS $c_name"
      ;;
    *)
      echo "FAILED to create $c_name: $(describe_failure "$c_http" "$WORK_DIR/model-new.json")" >&2
      return 1
      ;;
  esac
}

# verify_serves <model_name>
#
# One 1-token completion through the master key. The master key is used rather
# than the fabro key because this asks "does the row work?", not "may fabro call
# it?" — the allowlist is checked separately. A busy box holds the request in
# LiteLLM's per-deployment semaphore, so a client timeout is reported as
# inconclusive rather than as a failure: this script must not fail a correct
# provisioning because a run happened to be mid-turn.
verify_serves() {
  s_name="$1"

  s_payload=$(jq -nc --arg m "$s_name" \
    '{model:$m, messages:[{role:"user",content:"ok"}], max_tokens:1, temperature:0}')

  s_http=$(curl -sS -m 30 -o "$WORK_DIR/smoke.json" -w '%{http_code}' \
    -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
    -d "$s_payload" "$API_URL/v1/chat/completions" 2>/dev/null) || s_http=000

  case "$s_http" in
    200)
      echo "serves: $s_name answered a completion"
      ;;
    000)
      echo "INCONCLUSIVE: $s_name did not answer within 30s; its box is probably mid-turn. Call it by hand once the box is idle." >&2
      ;;
    *)
      echo "FAILED: $s_name answered HTTP $s_http: $(head -c 300 "$WORK_DIR/smoke.json" 2>/dev/null || true)" >&2
      return 1
      ;;
  esac
}

# verify_coder_row <model_name> <api_base>
#
# Observation only, for the two `coders` rows this script deliberately does not
# manage. A missing one is reported because the whole design leans on `coders`
# staying the fallback for every unpinned and hand-fired run (overview decision
# 12), but recreating it by hand is an operator decision, not this script's.
verify_coder_row() {
  v_name="$1"
  v_base="$2"

  v_match=$(jq -r --arg n "$v_name" --arg b "$v_base" \
    '[.data[]? | select(.model_name == $n and .litellm_params.api_base == $b)] | length' \
    "$MODELS_JSON")

  if [ "$v_match" -gt 0 ]; then
    echo "already present: $v_name -> $v_base"
    return 0
  fi

  echo "DRIFT: $v_name -> $v_base is missing from the live pool. Not recreated; every unpinned and hand-fired run falls back to that group." >&2
  DRIFT_FOUND=1
  return 0
}

# grant_key_access
#
# Add both new names to the fabro key's model allowlist if they are missing.
# `POST /key/update` replaces the whole `models` array, so this reads the
# current list first and only calls it when something is genuinely absent. An
# empty allowlist means unrestricted and always permits everything.
grant_key_access() {
  if [ -z "$FABRO_KEY" ]; then
    echo "SKIPPED: LITELLM_FABRO_KEY is not set, so the fabro key's model allowlist was not touched." >&2
    echo "         A run pinned to $CODER_A or $CODER_B will fail at the model call with" >&2
    echo "         '403 key not allowed to access model' until it is granted." >&2
    return 0
  fi

  k_http=$(curl -sS -m 15 -o "$WORK_DIR/key-info.json" -w '%{http_code}' \
    -H "$AUTH_HEADER" "$API_URL/key/info?key=$FABRO_KEY" 2>/dev/null) || k_http=000

  if [ "$k_http" != 200 ]; then
    echo "FAILED to read the fabro key's info: $(describe_failure "$k_http" "$WORK_DIR/key-info.json")" >&2
    return 1
  fi

  k_models=$(jq -c '.info.models // []' "$WORK_DIR/key-info.json" 2>/dev/null || echo '[]')

  if [ "$k_models" = "[]" ]; then
    echo "already present: the fabro key is unrestricted (models: []), both new names are callable"
    return 0
  fi

  k_missing=$(jq -nc --argjson models "$k_models" --arg a "$CODER_A" --arg b "$CODER_B" \
    '[$a, $b] | map(. as $n | select(($models | index($n)) == null))')

  if [ "$k_missing" = "[]" ]; then
    echo "already present: the fabro key already permits $CODER_A and $CODER_B"
    return 0
  fi

  k_updated=$(jq -nc --argjson models "$k_models" --argjson missing "$k_missing" \
    '$models + $missing')

  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN: would add $k_missing to the fabro key's allowlist, giving models: $k_updated"
    return 0
  fi

  k_payload=$(jq -nc --arg key "$FABRO_KEY" --argjson models "$k_updated" \
    '{key: $key, models: $models}')

  k_up_http=$(curl -sS -m 15 -o "$WORK_DIR/key-update.json" -w '%{http_code}' \
    -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
    -d "$k_payload" "$API_URL/key/update" 2>/dev/null) || k_up_http=000

  case "$k_up_http" in
    200 | 201)
      echo "granted: the fabro key can now call $k_missing"
      ;;
    *)
      echo "FAILED to grant the fabro key access to $k_missing: $(describe_failure "$k_up_http" "$WORK_DIR/key-update.json")" >&2
      return 1
      ;;
  esac
}

echo "Provisioning per-box coder model groups..."

# A placeholder credential, not a secret: llama.cpp does not authenticate. See
# the header for why the row cannot be built without one.
CODER_API_KEY="sk-litellm-local-no-auth"

# Both rows are `openai/deepseek-v4-flash-0731-iq3-xxs` on a plain-http LAN
# llama.cpp, so they carry no *real* upstream credential and `GET
# /credentials/by_model/{id}` is empty for them. `litellm_params` is copied
# field-for-field from the existing `coders` rows (read back from
# /v1/model/info on 2026-09-18) apart from `model_name`: `timeout` 600.0 is the
# value commit c14b538 set after 120s was cutting real coder turns, and
# `max_parallel_requests` 1 mirrors llama.cpp's single slot per box. Dropping
# either one here would reintroduce the silent stall that 2026-09-17's
# concurrency tuning fixed.
CODER_PARAMS_29=$(jq -nc --arg key "$CODER_API_KEY" \
  '{api_base:"http://10.10.0.29:8000/v1",timeout:600,allow_client_keepalive_override:false,use_in_pass_through:false,use_litellm_proxy:false,use_xai_oauth:false,merge_reasoning_content_in_choices:false,model:"openai/deepseek-v4-flash-0731-iq3-xxs",max_parallel_requests:1,api_key:$key}')
CODER_PARAMS_56=$(jq -nc --arg key "$CODER_API_KEY" \
  '{api_base:"http://10.10.0.56:8000/v1",timeout:600,allow_client_keepalive_override:false,use_in_pass_through:false,use_litellm_proxy:false,use_xai_oauth:false,merge_reasoning_content_in_choices:false,model:"openai/deepseek-v4-flash-0731-iq3-xxs",max_parallel_requests:1,api_key:$key}')

provision_coder_group \
  "$CODER_A" "$CODER_PARAMS_29" \
  "Coder box .29, pinned by the scheduler. Same deployment as coders; one box, one run." \
  || FAILED=$((FAILED+1))

provision_coder_group \
  "$CODER_B" "$CODER_PARAMS_56" \
  "Coder box .56, pinned by the scheduler. Same deployment as coders; one box, one run." \
  || FAILED=$((FAILED+1))

# Read-only: these two are the load-balanced fallback and are never written here.
verify_coder_row "coders" "http://10.10.0.29:8000/v1"
verify_coder_row "coders" "http://10.10.0.56:8000/v1"

# Counted like every other step, NOT `|| true`. The unset-key case returns 0 on its
# own after saying SKIPPED, so swallowing a non-zero here could only ever hide a real
# failure -- and the one it would hide is the worst one this script has. A key that
# cannot see `coders-a` answers `403 key not allowed to access model` on every pinned
# turn, which is the trap the header opens with and which cost a live issue-triage
# fire on 2026-09-16. Exiting 0 on it would tell a wrapper, and the deployment log,
# that the pin is ready when it is not.
grant_key_access || FAILED=$((FAILED+1))

# Only rows created just now. Nothing was created on a re-run, so a re-run
# issues no completion and no POST at all.
if [ "$VERIFY" = "1" ] && [ "$DRY_RUN" != "1" ] && [ -n "$CREATED_MODELS" ]; then
  for s_model in $CREATED_MODELS; do
    verify_serves "$s_model" || FAILED=$((FAILED+1))
  done
fi

if [ "$FAILED" -ne 0 ]; then
  echo "$FAILED step(s) failed; see the errors above." >&2
  exit 1
fi

if [ "$DRIFT_FOUND" -ne 0 ]; then
  echo "Done, with drift reported above. Reconcile those rows by hand." >&2
  exit 2
fi

echo "Done. A pinned run now needs only the stylesheet change (docs/scheduler/05-coder-pool-stylesheets.md)."
