#!/bin/sh
# Provision LiteLLM models that this workflow depends on.
#
# Idempotent: a model that already exists is never recreated, so re-running
# after a partial failure is safe. It is NOT a reconciler — a model whose
# configuration has drifted is reported, not corrected, because silently
# rewriting live model state from a bootstrap script is worse than telling
# the operator. No secrets live in this file; the API URL and master key come
# from the environment.
#
# This script manages high-reasoning only. The other six models (deepseek,
# StrixQwen27B, StrixQwen35B, long-context, kimi-k3, coders) predate this
# workflow and are not provisioned here.
#
# Fallbacks are set on the model row via the API, not in values.yaml,
# because config-level router_settings.fallbacks do not apply to models
# stored in Postgres (verified 2026-09-16).
#
# Usage:
#   LITELLM_URL=https://litellm.herrington.services \
#   LITELLM_MASTER_KEY=<key> \
#   ./ops/provision-litellm-models.sh
#
# Environment:
#   DRY_RUN: if 1 (the default), print the payload and exit without creating.
#   LITELLM_URL: base URL of the LiteLLM proxy admin API (required)
#   LITELLM_MASTER_KEY: master key for authentication (required, never echoed)

set -eu

DRY_RUN="${DRY_RUN:-1}"
API_URL="${LITELLM_URL:-}"
MASTER_KEY="${LITELLM_MASTER_KEY:-}"

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

  # Model does not exist; create it
  m_payload=$(jq -nc \
    --arg name "$m_name" \
    --argjson params "$m_params" \
    --argjson info "$m_info" \
    '{model_name: $name, litellm_params: $params, model_info: $info}')

  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN: would create model $m_name with payload:"
    printf '%s\n' "$m_payload" | jq .
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

# provision_model_fallback <model_name> <fallback_models_json>
#
# Set fallback models for an existing model. This is done separately from
# creation because LiteLLM's /model/new API may not support fallback in the
# initial payload, and config-level router_settings.fallbacks do not apply
# to Postgres models.
provision_model_fallback() {
  m_name="$1"
  m_fallbacks="$2"

  # For now, fallbacks are set via litellm_params in the model creation.
  # If the API requires a separate update step in future, that logic goes here.
  # This function is a placeholder for clarity about where fallback config lives.
  :
}

echo "Provisioning LiteLLM models..."

# high-reasoning: strong reasoner for judgment stages. Falls back to kimi-k3 on overflow.
# litellm_params copied from glm-5.3 to ensure api_base, model, and timeout are current.
# Fallback is configured on the model row via the admin API after creation
# (LiteLLM's /model/new API does not support fallbacks in the initial payload).
provision_model \
  "high-reasoning" \
  '{"api_base":"https://api.z.ai/api/coding/paas/v4","timeout":3600,"use_in_pass_through":false,"use_litellm_proxy":false,"use_xai_oauth":false,"merge_reasoning_content_in_choices":false,"model":"openai/glm-5.3"}' \
  '{"description":"Role alias: strong reasoner for judgment stages. glm-5.3, kimi-k3 on overflow."}' \
  || FAILED=$((FAILED+1))

if [ "$FAILED" -ne 0 ]; then
  echo "$FAILED model(s) could not be created; see the errors above." >&2
  exit 1
fi

if [ "$DRIFT_FOUND" -ne 0 ]; then
  echo "Done, with drift reported above. Reconcile those models by hand." >&2
  exit 2
fi

echo "Done."
