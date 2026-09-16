#!/bin/sh
# Provision LiteLLM models that this workflow depends on.
#
# Idempotent: a model that already exists is never recreated, so re-running
# after a partial failure is safe. It is NOT a reconciler — a model whose
# configuration has drifted is reported, not corrected, because silently
# rewriting live model state from a bootstrap script is worse than telling
# the operator. No secrets live in this file; the API URL, master key and
# upstream credential come from the environment.
#
# This script manages high-reasoning only. The other six models (deepseek,
# StrixQwen27B, StrixQwen35B, long-context, kimi-k3, coders) predate this
# workflow and are not provisioned here.
#
# Fallbacks are set via LiteLLM's Fallback Management API (POST/GET
# /fallback), not in values.yaml: config-level router_settings.fallbacks in
# the Helm chart's values.yaml do not reach the router LiteLLM actually runs
# once STORE_MODEL_IN_DB=True — /fallback is the git-invisible, Postgres-
# backed equivalent, and it is what the acceptance check in
# docs/issue-triage/01-litellm-high-reasoning.md proves against (verified
# 2026-09-16 with a deliberately-broken primary credential: the response came
# back from kimi-k3 with x-litellm-attempted-fallbacks: 1).
#
# GET /model/info never returns litellm_params.api_key (LiteLLM stores it in
# a separate per-model credential row and omits it from every read view, even
# to the master key), so this script cannot detect credential drift — only
# whether the row exists at all. The credential is therefore set once, at
# creation, and never touched again on a re-run; an operator who rotates it
# later does so directly against LiteLLM, not through this script.
#
# A model existing is not the same as fabro being able to call it: LiteLLM
# virtual keys carry their own model allowlist (`key/info`'s `models` field),
# independent of whether the model itself exists. Creating high-reasoning
# without adding it to the fabro key's allowlist produces a model that
# validates and lists fine but fails every real call with "key not allowed to
# access model" — found 2026-09-16 on the first live fire of issue-triage,
# after this script had already reported high-reasoning provisioned
# successfully. This script therefore also grants the fabro key access.
#
# Usage:
#   LITELLM_URL=https://litellm.herrington.services \
#   LITELLM_MASTER_KEY=<key> \
#   LITELLM_HIGH_REASONING_API_KEY=<the z.ai key glm-5.3 already uses> \
#   LITELLM_FABRO_KEY=<the virtual key fabro's workflows call litellm with> \
#   ./ops/provision-litellm-models.sh
#
# Environment:
#   DRY_RUN: if 1 (the default), print the payload and exit without creating.
#   LITELLM_URL: base URL of the LiteLLM proxy admin API (required)
#   LITELLM_MASTER_KEY: master key for authentication (required, never echoed)
#   LITELLM_HIGH_REASONING_API_KEY: upstream z.ai key for the model (required
#     to create; not required, and ignored, when the model already exists)
#   LITELLM_FABRO_KEY: the vault's LITELLM_API_KEY / FABRO_LITELLM_KEY value —
#     the virtual key fabro workflows authenticate to litellm with (required
#     to grant access; not required, and ignored, if it already has access)

set -eu

DRY_RUN="${DRY_RUN:-1}"
API_URL="${LITELLM_URL:-}"
MASTER_KEY="${LITELLM_MASTER_KEY:-}"
UPSTREAM_KEY="${LITELLM_HIGH_REASONING_API_KEY:-}"
FABRO_KEY="${LITELLM_FABRO_KEY:-}"

if [ -z "$API_URL" ] || [ -z "$MASTER_KEY" ]; then
  echo "Both LITELLM_URL and LITELLM_MASTER_KEY must be set." >&2
  exit 1
fi

AUTH_HEADER="Authorization: Bearer $MASTER_KEY"

# Set by provision_model when an existing row does not match this script.
DRIFT_FOUND=0
# Incremented when a model could not be created.
FAILED=0

# Read the model list once, up front. Fetch first, then filter: piping
# curl straight into jq takes jq's exit status, so an API outage would come
# back as empty output and be indistinguishable from "nothing exists yet" —
# the script would then POST every model and report misleading errors.
if ! curl -fsS -m 15 -H "$AUTH_HEADER" "$API_URL/model/info" > /tmp/provision_models.json 2>/dev/null; then
  echo "FAILED to list models from $API_URL; cannot tell what already exists." >&2
  exit 1
fi

# provision_model <model_name> <litellm_params_json> <model_info_json>
#
# Create a model if it does not exist. If it exists but has different
# litellm_params, report drift. If it matches, report exists.
provision_model() {
  m_name="$1"
  m_params="$2"
  m_info="$3"

  m_exists=$(jq -r --arg n "$m_name" '.data[]? | select(.model_name == $n) | .model_name // ""' /tmp/provision_models.json)

  if [ -n "$m_exists" ]; then
    m_existing_model=$(jq -r --arg n "$m_name" \
      '.data[]? | select(.model_name == $n) | .litellm_params.model // ""' /tmp/provision_models.json)
    m_provided_model=$(printf '%s' "$m_params" | jq -r '.model // ""')

    if [ "$m_existing_model" = "$m_provided_model" ]; then
      echo "exists: $m_name"
      return 0
    else
      echo "DRIFT: $m_name has model=$m_existing_model, expected $m_provided_model. Not corrected." >&2
      DRIFT_FOUND=1
      return 0
    fi
  fi

  # Model does not exist; create it. The upstream credential is required here
  # — it can never be added later by re-running this script (see header) —
  # but is irrelevant, and not required to be set, once the model exists.
  if [ -z "$UPSTREAM_KEY" ]; then
    echo "FAILED to create $m_name: LITELLM_HIGH_REASONING_API_KEY is not set." >&2
    FAILED=$((FAILED+1))
    return 1
  fi

  m_payload=$(jq -nc \
    --arg name "$m_name" \
    --argjson params "$m_params" \
    --argjson info "$m_info" \
    --arg key "$UPSTREAM_KEY" \
    '{model_name: $name, litellm_params: ($params + {api_key: $key}), model_info: $info}')

  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN: would create model $m_name with payload (api_key redacted):"
    printf '%s\n' "$m_payload" | jq '.litellm_params.api_key = "<redacted>"'
    return 0
  fi

  m_http=$(curl -sS -m 15 -o /tmp/provision_model_out.json -w '%{http_code}' \
    -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
    -d "$m_payload" "$API_URL/model/new" 2>/dev/null) || m_http=000

  case "$m_http" in
    200 | 201)
      echo "created: $m_name"
      ;;
    *)
      echo "FAILED to create $m_name: HTTP $m_http $(cat /tmp/provision_model_out.json 2>/dev/null || true)" >&2
      FAILED=$((FAILED+1))
      return 1
      ;;
  esac
}

# provision_fallback <model_name> <fallback_models_json_array> <fallback_type>
#
# Create-if-absent against LiteLLM's Fallback Management API. GET returns 404
# when nothing is configured yet; anything else that does not already match
# is reported as drift and left alone — POST /fallback is documented as
# create-or-update, so calling it over an operator's existing, different
# configuration would silently overwrite it.
provision_fallback() {
  f_name="$1"
  f_models="$2"
  f_type="$3"

  f_http=$(curl -sS -m 15 -o /tmp/provision_fallback_get.json -w '%{http_code}' \
    -H "$AUTH_HEADER" "$API_URL/fallback/$f_name?fallback_type=$f_type" 2>/dev/null) || f_http=000

  if [ "$f_http" = 200 ]; then
    f_existing=$(jq -c '.fallback_models // []' /tmp/provision_fallback_get.json 2>/dev/null || echo '[]')
    f_expected=$(printf '%s' "$f_models" | jq -c '.')
    if [ "$f_existing" = "$f_expected" ]; then
      echo "exists: fallback $f_name -> $f_expected ($f_type)"
      return 0
    fi
    echo "DRIFT: fallback $f_name has $f_existing, expected $f_expected ($f_type). Not corrected." >&2
    DRIFT_FOUND=1
    return 0
  fi
  if [ "$f_http" != 404 ]; then
    echo "FAILED to read fallback for $f_name: HTTP $f_http" >&2
    FAILED=$((FAILED+1))
    return 1
  fi

  f_payload=$(jq -nc --arg m "$f_name" --argjson fm "$f_models" --arg t "$f_type" \
    '{model: $m, fallback_models: $fm, fallback_type: $t}')

  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN: would create fallback with payload:"
    printf '%s\n' "$f_payload" | jq .
    return 0
  fi

  f_create_http=$(curl -sS -m 15 -o /tmp/provision_fallback_out.json -w '%{http_code}' \
    -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
    -d "$f_payload" "$API_URL/fallback" 2>/dev/null) || f_create_http=000

  case "$f_create_http" in
    200 | 201)
      echo "created: fallback $f_name -> $(printf '%s' "$f_models" | jq -c '.') ($f_type)"
      ;;
    *)
      echo "FAILED to create fallback for $f_name: HTTP $f_create_http $(cat /tmp/provision_fallback_out.json 2>/dev/null || true)" >&2
      FAILED=$((FAILED+1))
      return 1
      ;;
  esac
}

# provision_key_access <model_name>
#
# Add <model_name> to the fabro-scoped LiteLLM key's allowlist if it is not
# already there. LiteLLM's /key/update replaces the whole `models` array, so
# this reads the current list first and only calls it when the model is
# genuinely missing — an empty/unrestricted key (models: []) already permits
# everything and is left alone.
provision_key_access() {
  k_model="$1"

  if [ -z "$FABRO_KEY" ]; then
    echo "SKIPPED: cannot check/grant $k_model access on the fabro key — LITELLM_FABRO_KEY is not set." >&2
    return 0
  fi

  k_http=$(curl -sS -m 15 -o /tmp/provision_keyinfo.json -w '%{http_code}' \
    -H "$AUTH_HEADER" "$API_URL/key/info?key=$FABRO_KEY" 2>/dev/null) || k_http=000
  if [ "$k_http" != 200 ]; then
    echo "FAILED to read fabro key info: HTTP $k_http" >&2
    FAILED=$((FAILED+1))
    return 1
  fi

  k_models=$(jq -c '.info.models // []' /tmp/provision_keyinfo.json 2>/dev/null || echo '[]')
  if [ "$k_models" = "[]" ]; then
    echo "exists: fabro key is unrestricted (models: []), $k_model already reachable"
    return 0
  fi
  if printf '%s' "$k_models" | jq -e --arg m "$k_model" 'index($m) != null' > /dev/null 2>&1; then
    echo "exists: fabro key already permits $k_model"
    return 0
  fi

  k_updated=$(printf '%s' "$k_models" | jq -c --arg m "$k_model" '. + [$m]')

  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN: would grant fabro key access to $k_model (models -> $k_updated)"
    return 0
  fi

  k_payload=$(jq -nc --arg key "$FABRO_KEY" --argjson models "$k_updated" '{key: $key, models: $models}')
  k_up_http=$(curl -sS -m 15 -o /tmp/provision_keyupdate.json -w '%{http_code}' \
    -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
    -d "$k_payload" "$API_URL/key/update" 2>/dev/null) || k_up_http=000

  case "$k_up_http" in
    200 | 201)
      echo "granted: fabro key can now call $k_model"
      ;;
    *)
      echo "FAILED to grant fabro key access to $k_model: HTTP $k_up_http $(cat /tmp/provision_keyupdate.json 2>/dev/null || true)" >&2
      FAILED=$((FAILED+1))
      return 1
      ;;
  esac
}

echo "Provisioning LiteLLM models..."

# high-reasoning: strong reasoner for judgment stages. Falls back to kimi-k3 on overflow.
# litellm_params copied from glm-5.3 (model, api_base, timeout) plus the upstream
# credential, which glm-5.3's own row shares and which /model/info never exposes.
provision_model \
  "high-reasoning" \
  '{"api_base":"https://api.z.ai/api/coding/paas/v4","timeout":3600,"use_in_pass_through":false,"use_litellm_proxy":false,"use_xai_oauth":false,"merge_reasoning_content_in_choices":false,"model":"openai/glm-5.3"}' \
  '{"description":"Role alias: strong reasoner for judgment stages. glm-5.3, kimi-k3 on overflow."}' \
  || FAILED=$((FAILED+1))

# Do not add a reverse fallback. kimi-k3 -> high-reasoning recreates the
# circular chain ADR 0001 flagged in the other two workflows.
provision_fallback "high-reasoning" '["kimi-k3"]' "general" || true

provision_key_access "high-reasoning" || true

if [ "$FAILED" -ne 0 ]; then
  echo "$FAILED model(s) could not be created; see the errors above." >&2
  exit 1
fi

if [ "$DRIFT_FOUND" -ne 0 ]; then
  echo "Done, with drift reported above. Reconcile those models by hand." >&2
  exit 2
fi

echo "Done."
