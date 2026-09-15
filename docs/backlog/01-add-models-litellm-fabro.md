# Task 01 — Add `glm-4.7` and `kimi-for-coding` to LiteLLM + fabro

**Depends on:** nothing. **Do before:** task 09 (the new workflow's stylesheet needs these models).
**LLM level:** local is fine — exact commands below.

## Goal

Make two new models callable through the shared LiteLLM proxy and registered in the
fabro server, so the redesigned workflow's escalation ladder
(`coders → glm-4.7 → kimi-for-coding → glm-5.3`) resolves.

- `glm-4.7` → z.ai coding plan, OpenAI protocol, upstream model id `glm-4.7`
- `kimi-for-coding` → Kimi coding plan, Anthropic protocol, upstream model id `kimi-for-coding` (K2.8 Preview, 1M ctx)

Both upstreams are the same endpoints/keys already used by `glm-5.3` and `kimi-k3`.
Verified available: `GET https://api.z.ai/api/coding/paas/v4/models` lists `glm-4.7`;
`GET https://api.kimi.com/coding/v1/models` lists `kimi-for-coding`.

## Key facts / paths

- LiteLLM admin: `lite` CLI on the Mac with env:
  `LITELLM_PROXY_URL=https://litellm.herrington.services`
  `LITELLM_PROXY_API_KEY` — copy from `FABRO-DEPLOYMENT-PLAN.md` §"Inputs"
  (also usable: the value in that file). Never echo the key into logs/files.
- Upstream keys: `ZAI_API_KEY` and `KIMI_API_KEY` in `~/.fabro-deploy/env` (Mac, chmod 600).
- The fabro virtual key (alias `fabro`) is currently scoped to
  `coders,long-context,kimi-k3,glm-5.3` — **it must be extended to include the two
  new models or every fabro call to them will 401.**
- Fabro server settings live inside the container at `/storage/.home/settings.toml`
  (host: `ssh andrew@10.10.0.32`). The host CLI is authed (`fabro secret set` works).
- Backup discipline: before editing `settings.toml`, copy it to `settings.toml.bak2`.

## Steps

### 1. Add the two LiteLLM deployments (on the Mac)

Write `/tmp/fabro-models-add.yaml` (fill keys from `~/.fabro-deploy/env`; same
"option (b)" literal-key approach used in Phase 1 of the original deployment):

```yaml
model_list:
  - model_name: glm-4.7
    litellm_params:
      model: openai/glm-4.7
      api_base: https://api.z.ai/api/coding/paas/v4
      api_key: <ZAI_API_KEY value>
      timeout: 600
  - model_name: kimi-for-coding
    litellm_params:
      model: anthropic/kimi-for-coding
      api_base: https://api.kimi.com/coding
      api_key: <KIMI_API_KEY value>
      timeout: 600
```

```bash
export LITELLM_PROXY_URL=https://litellm.herrington.services
export LITELLM_PROXY_API_KEY=<from FABRO-DEPLOYMENT-PLAN.md>
lite models import /tmp/fabro-models-add.yaml --dry-run   # expect: 2 deployments
lite models import /tmp/fabro-models-add.yaml
shred -u /tmp/fabro-models-add.yaml 2>/dev/null || rm -P /tmp/fabro-models-add.yaml
```

### 2. Extend the fabro virtual key's model list

Check the CLI's capabilities first: `lite keys --help` and `lite keys update --help`.
If an update-in-place exists, use it to set the `fabro` key's models to
`coders,long-context,kimi-k3,glm-5.3,glm-4.7,kimi-for-coding`.

If update-in-place is NOT supported: regenerate the key
(`lite keys delete --key-aliases fabro`, then
`lite keys generate --models coders,long-context,kimi-k3,glm-5.3,glm-4.7,kimi-for-coding --key-alias fabro`),
save the new key, and update both stores:

```bash
# Mac env file (keep chmod 600)
# update FABRO_LITELLM_KEY in ~/.fabro-deploy/env
# fabro vault on the host:
ssh andrew@10.10.0.32 '~/.fabro/bin/fabro secret set LITELLM_API_KEY "<new key>"'
```

### 3. Verify LiteLLM routing (on the Mac)

Use the fabro virtual key (from `~/.fabro-deploy/env`):

```bash
for m in glm-4.7 kimi-for-coding; do
  curl -fsS -m 60 -X POST $LITELLM_PROXY_URL/v1/chat/completions \
    -H "Authorization: Bearer $FABRO_LITELLM_KEY" -H 'Content-Type: application/json' \
    -d "{\"model\": \"$m\", \"messages\": [{\"role\": \"user\", \"content\": \"Reply with exactly: ok\"}], \"max_tokens\": 32}" \
    >/dev/null && echo "$m: ok" || echo "$m: FAIL"
done
```

Both must print `ok`. (kimi-for-coding is a reasoning model: `content` may be empty
with `finish_reason: length` on tiny max_tokens — an HTTP 200 still counts as ok.
If in doubt retry with `"max_tokens": 256`.)

### 4. Register both models in fabro settings (on the host)

```bash
ssh andrew@10.10.0.32
docker exec fabro-fabro-1 cp /storage/.home/settings.toml /storage/.home/settings.toml.bak2
docker exec fabro-fabro-1 cat /storage/.home/settings.toml > /tmp/settings.toml
```

Append to `/tmp/settings.toml` (matches the style of the existing four model blocks):

```toml
[llm.providers.litellm.models."glm-4.7"]
display_name = "GLM 4.7 (z.ai coding plan)"
api_model = "glm-4.7"
limits = { context_tokens = 202752, max_output_tokens = 16384 }
capabilities = { text = true, tools = true, reasoning = true }

[llm.providers.litellm.models."kimi-for-coding"]
display_name = "Kimi for Coding (K2.8, coding plan)"
api_model = "kimi-for-coding"
limits = { context_tokens = 1048576, max_output_tokens = 16384 }
capabilities = { text = true, tools = true, reasoning = true }
```

Install + restart + verify:

```bash
docker cp /tmp/settings.toml fabro-fabro-1:/storage/.home/settings.toml
docker exec fabro-fabro-1 chown fabro:fabro /storage/.home/settings.toml
cd ~/fabro && docker compose restart fabro && sleep 8 && curl -fsS http://127.0.0.1:32276/health
```

### 5. Verify fabro-side routing

```bash
ssh andrew@10.10.0.32 '~/.fabro/bin/fabro model list --provider litellm'
# expect all six: coders, glm-4.7, glm-5.3, kimi-for-coding, kimi-k3, long-context

TOK=<dev token from FABRO-DEPLOYMENT-LOG.md>
for m in glm-4.7 kimi-for-coding; do
  curl -fsS -m 60 -H "Authorization: Bearer $TOK" -X POST \
    http://10.10.0.32:32276/api/v1/models/$m/test; echo
done
```

Both must return `"status":"ok"`. Retry once on transient failure (shared upstreams).

## Done when

- `lite models list` shows `glm-4.7` and `kimi-for-coding`.
- Both return completions via the **fabro** virtual key (proves key scope updated).
- `fabro model list --provider litellm` shows all six models; both new model tests return `status: ok`.
- `/tmp/fabro-models-add.yaml` deleted; no key material written to any repo file.

## Pitfalls

- Do NOT touch the existing `deepseek`, `StrixQwen*`, `coders`, `long-context`,
  `kimi-k3`, `glm-5.3` deployments.
- If the fabro key is regenerated, nothing else needs changing: fabro only knows
  `LITELLM_API_KEY` from the vault; the model deployments are keyed by name.
- If `model test` says "does not support reasoning", the `capabilities` line was
  missed — re-check step 4.
