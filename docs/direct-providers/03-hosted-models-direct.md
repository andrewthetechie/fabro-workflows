# Move the hosted models to `zai` and `kimi`

## Outcome

`glm-4.7`, `glm-5.3`, `high-reasoning`, `kimi-for-coding` and `kimi-k3` resolve to two new
providers, and no `litellm:` target remains in a workflow TOML. Fabro holds both keys.

## Context Pack

- Overview: the mapping table, findings 1, 3 and 4, and the model-id contract.
- ADR 0007: the key-custody consequence and the ADR 0003 supersession.
- Thirteen `litellm:` fallback targets exist across two TOMLs, and this task changes all of
  them. Task 01 changes none, because it keeps every affected model id.
- Kimi is Anthropic-shaped. LiteLLM reaches it as `anthropic/kimi-for-coding`.
- `high-reasoning` is a role alias today, and ADR 0003 put its `kimi-k3` overflow inside
  LiteLLM. That overflow moves to `[run.model.fallbacks]`.

## Implementation Contract

1. Put both keys in the fabro vault. Read them from `~/.fabro-deploy/env` on the Mac. No
   value belongs in this repository.
2. Add two providers.

   **A provider name fabro does not know built-in must declare `display_name`.** Fabro
   knows `zai`, so a bare model table extends it and needs no provider table. Fabro does
   not know `kimi` — its built-in name for those models is `moonshot` — so `kimi` is a new
   provider and needs the full table. Omitting `display_name` there fails the whole `[llm]`
   layer with `catalog layer "settings [llm]" is invalid TOML: missing field
   display_name`, and the failure is close to silent: the server rejects the reload and
   keeps the previous catalog, so `GET /settings` and `fabro model list` both keep
   answering with the good one. Only a **worker** reads the file, so every new run dies at
   its first node with `Worker exited before emitting a terminal run event: exit status:
   1`. That cost 14 hours on 2026-09-20 — see the deployment log.

```toml
[llm.providers.zai.models."glm-5.3"]
display_name = "GLM 5.3 (z.ai coding plan)"
api_model = "glm-5.3"
aliases = ["high-reasoning"]
limits = { context_tokens = 202752, max_output_tokens = 16384 }
capabilities = { text = true, tools = true, reasoning = true }

[llm.providers.kimi]
display_name = "Kimi (coding plan)"
base_url = "https://api.kimi.com/coding"
enabled = true
adapter = "anthropic"
codec = "anthropic-messages"
auth = { type = "bearer" }

[llm.providers.kimi.models."kimi-for-coding"]
display_name = "Kimi for Coding (coding plan)"
api_model = "kimi-for-coding"
limits = { context_tokens = 1048576, max_output_tokens = 16384 }
capabilities = { text = true, tools = true, reasoning = true }
```
   `glm-4.7` resolves on built-in `zai` and needs no row. `kimi-k3` follows `kimi-for-coding`
   on `kimi`.

   Watch the 5s poll confirm the edit before you walk away. `docker logs --since 1m
   fabro-fabro-1 | grep -c "Rejected reloaded"` must print `0`. A non-zero count means the
   catalog was refused and the next run will die.
3. Delete those five rows from the `litellm` provider.
4. Rewrite the thirteen fallback targets. `litellm:glm-4.7` and `litellm:glm-5.3` become
   `zai:...`. `litellm:kimi-for-coding` and `litellm:kimi-k3` become `kimi:...`. The keys
   stay as they are, because fabro keys a fallback on the requested model name.
5. Reverse ADR 0003's consequence. `issue-triage` now carries a `[run.model.fallbacks]`
   entry for `high-reasoning`, because the overflow that made one unnecessary lived in
   LiteLLM.
6. Change no model id and no graph.
7. Apply with no restart.

## Acceptance Criteria

- [ ] `fabro model list` reports all five models against the two new providers.
- [ ] `fabro model test high-reasoning` and `fabro model test kimi-for-coding` pass.
- [ ] `grep -rn 'litellm:' .fabro/workflows/` returns nothing.
- [ ] `grep -n 'high-reasoning' .fabro/workflows/issue-triage/workflow.toml` shows a
      fallback entry.
- [ ] One run reaches a `high-reasoning` stage, and one reaches a Kimi stage.
- [ ] `fabro validate` reports the recorded baselines for all three packages.

## Dependencies

Blocked by operator acceptance of the key move, not by code. Task 01 is independent.

## Risk

4. Two wrong adapter or codec pairs fail every hosted stage, and the hosted models are the
fallback for a broken box. The Kimi pair has no precedent in this repository.
