# Task 01 — LiteLLM: the `high-reasoning` model, its overflow, and a provisioning script

**Depends on:** nothing. **Blocks:** 02, 03, 08. **LLM level:** ordinary — API calls and
a POSIX script.

Create one LiteLLM model named `high-reasoning` that resolves to `glm-5.3` and falls
back to `kimi-k3`, then make it reproducible. ADR 0003 has the reasoning, including why
this is not a two-member pool.

## Where this state lives

`~/Documents/code/home-k8s/nauvoo-v2/manifests/apps/litellm/values.yaml` sets
`proxy_config.model_list: []` with `envVars.STORE_MODEL_IN_DB: "True"`. **Every model is
a row in Postgres**, created through the Admin UI or the admin API at
`https://litellm.herrington.services`, and nothing in git records that any of them
exist. The master key is the sealed secret `litellm-secrets`, key `masterkey`.

`coders` is already exactly this shape — one `model_name` with two local deployments —
so read it back before writing anything and copy its structure:

```sh
curl -fsS -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  https://litellm.herrington.services/model/info | jq '.data[] | {model_name, litellm_params}'
```

## Part 1 — the model

One deployment, not two:

```json
{
  "model_name": "high-reasoning",
  "litellm_params": { "model": "<the same upstream target glm-5.3 uses>", "timeout": 3600 },
  "model_info": { "description": "Role alias: strong reasoner for judgment stages. glm-5.3, kimi-k3 on overflow." }
}
```

Take `litellm_params` from the existing `glm-5.3` row rather than retyping the upstream
model string, credentials reference and `timeout`. The 3600-second timeout is not
optional: `values.yaml` sets the ingress `proxy-read-timeout` to 3600 specifically
because these models exceed the NGINX default of 60s, and
`INCIDENT-2026-09-14-504.md` is what happens when the two disagree.

## Part 2 — the overflow

Prefer **`router_settings.fallbacks` in `values.yaml`**, because it is the one half of
this change that can live in git:

```yaml
proxy_config:
  model_list: []
  router_settings:
    fallbacks:
      - high-reasoning: ["kimi-k3"]
  general_settings:
    master_key: os.environ/PROXY_MASTER_KEY
```

Verify before committing that config-level `router_settings` still apply when models
come from the database — LiteLLM merges the two, but confirm it on this version rather
than trusting the docs. If it does not, set `fallbacks` on the model row instead and say
so in this file, because that moves the last git-visible piece of the routing into
Postgres.

Do **not** add a reverse fallback. `kimi-k3 → high-reasoning` recreates the circular
chain ADR 0001 flagged in the other two workflows.

## Part 3 — `ops/provision-litellm-models.sh`

Same contract as `ops/provision-server-state.sh`, which is the model to copy:

- POSIX `sh`, no secrets in the file — `LITELLM_URL` and `LITELLM_MASTER_KEY` from the
  environment, and the key never echoed.
- `DRY_RUN=1` by default, following the sweepers and `fire-pr-review.sh`.
- **Create if absent, report drift, never overwrite.** A re-provision that POSTs
  unconditionally would silently re-point a model an operator repointed during an
  incident. The same reasoning as `provision_variable` in `provision-server-state.sh`.
- Exit non-zero with a one-line reason on stderr when the proxy is unreachable or the
  key is rejected, so a cron or a human sees it.

Scope it to `high-reasoning` only. The other six models predate this and are not this
change's to adopt; add a comment saying so, or the next person will assume the script is
the whole catalog.

## Acceptance

- `curl .../model/info` lists `high-reasoning`.
- A completion against `high-reasoning` returns from glm-5.3:
  `curl -fsS -H "Authorization: Bearer $KEY" https://litellm.herrington.services/v1/chat/completions -d '{"model":"high-reasoning","messages":[{"role":"user","content":"reply with the single word ok"}]}'`
- The fallback is proven, not assumed: force it (a deliberately bad glm-5.3 credential,
  or LiteLLM's own `mock_testing_fallbacks`) and confirm the response comes from
  `kimi-k3`. An unproven fallback is an outage discovered under load.
- `DRY_RUN=1 ./ops/provision-litellm-models.sh` prints the payload and sends nothing;
  `DRY_RUN=0` on an already-provisioned proxy reports "exists" and changes nothing.
- `sh -n ops/provision-litellm-models.sh` passes.
