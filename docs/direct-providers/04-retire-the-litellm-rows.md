# Retire the fabro-side LiteLLM model rows

## Outcome

The overlay holds no `litellm` provider, `grep -rn 'litellm' .fabro/` returns nothing, and
the server's default model resolves.

## Context Pack

- Overview finding 5: `coders` is the server `default_model`, and utility calls such as
  generated run titles use it.
- ADR 0007: the consequences on `coders` and on LiteLLM keeping its other consumers.
- Tasks 01 and 03 remove eight of the nine rows. `coders` is what remains.

## Implementation Contract

1. Give `default_model` a target that is not one specific box. `spark` carries
   `small_default = true` and is the better target for a utility call. A single coder box
   as the target reintroduces unpinned box use, which the scheduler prevents.
2. Remove the `coders` row, or move it to `spark`.
3. Remove the `[llm.providers.litellm]` table.
4. Change no model id that a graph names. No graph names `coders`.
5. Leave LiteLLM running. It serves `StrixQwen27B` and `StrixQwen35B` on `10.10.0.29:8080`
   and `deepseek` on `10.10.0.30:8888`, and fabro references none of them.
6. Apply with no restart.

## Acceptance Criteria

- [ ] `docker exec fabro-fabro-1 cat /storage/.home/settings.toml` shows no `litellm`
      provider.
- [ ] `grep -rn 'litellm' .fabro/` returns nothing.
- [ ] `fabro model list` resolves the default model.
- [ ] A run completes a stage after the change, which proves the default model still
      resolves for a utility call.
- [ ] `fabro validate` reports the recorded baselines for all three packages.

## Dependencies

Blocked by task 03. Removing the provider before the hosted models move takes every hosted
stage down at once.

## Risk

3. Removing `coders` before `default_model` has a target breaks run-title generation
silently, and nothing in this repository gates it.
