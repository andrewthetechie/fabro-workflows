# Retire the fabro-side LiteLLM model rows

## Outcome

The overlay holds no `litellm` provider, `grep -rn 'litellm' .fabro/` returns nothing,
and the server's default model resolves. The last `coders` row is gone, and every
reference that used to name it now names a single pinned box.

## Context Pack

- Overview finding 5: `coders` was the server `default_model`, and utility calls such as
  generated run titles use it.
- ADR 0007: the consequences on `coders` and on LiteLLM keeping its other consumers.
- Tasks 01 and 03 removed eight of the nine rows. `coders` is what remained.
- **Operator decision (this task):** the task's original contract said "No graph names
  `coders`". That was wrong — the both-boxes `coders` group is load-bearing in three
  places beyond `default_model`: the `default('coders')` fallback in the `backlog` and
  `pr-review` root stylesheets, and `ops/fabro-fire-backlog.sh`, which fires the escape
  hatch with `coder_pool: "coders"`. Retiring the id with no other change would break
  an unpinned run and the escape hatch. The operator chose to retire `coders` outright
  and repoint every referent at the pinned box `coders-a`.

## Implementation Contract

1. Give `default_model` a target that is not one specific coder box. `spark` carries
   `small_default = true` and is the better target for a utility call, so the overlay
   sets `[llm.providers.spark] default_model = "long-context"` and `long-context`
   remains the `small_default` model. A single coder box as the target would
   reintroduce unpinned box use, which the scheduler prevents.
2. Remove the `coders` row and the whole `[llm.providers.litellm]` table. `coders` is
   retired, not re-homed: the "both-boxes" group it named existed to load-balance two
   boxes through the LiteLLM ingress, and that ingress is what this series removes.
3. Repoint the referents the original contract missed, because they name `coders`:
   - `backlog/workflow.fabro` root stylesheet: `* { model: coders-a; }` and
     `default('coders-a')` on `.coder`/`.rebase`.
   - `pr-review/workflow.fabro` root stylesheet: `default('coders-a')` on `.rebase`.
   - `ops/fabro-fire-backlog.sh`: `coder_pool: "coders-a"` (was `"coders"`).
   - The dead `coders = [...]` keys are removed from both `[run.model.fallbacks]`
     blocks (a fallback is keyed on the requested model name, which is never `coders`
     again).
4. Change no model id that a graph names. The ids that move are the two stylesheet
   *defaults* and the escape hatch's `coder_pool` value, both of which the scheduler
   overrides; `coders-a`/`coders-b`/`long-context` are untouched. No node, edge or
   prompt changes.
5. Leave LiteLLM running. It serves `StrixQwen27B` and `StrixQwen35B` on
   `10.10.0.29:8080` and `deepseek` on `10.10.0.30:8888`, and fabro references none of
   them. A hand-fired run now pins `coders-a`, so run the escape hatch only when the
   scheduler is not dispatching to that box.
6. Apply with no restart (`replace_runtime_settings` rebuilds the catalog on the 5s
   poll; `max_concurrent_runs` is the only value that needs a restart, and nothing here
   changes it).

## Acceptance Criteria

- [ ] `docker exec fabro-fabro-1 cat /storage/.home/settings.toml` shows no `litellm`
      provider.
- [ ] `grep -rn 'litellm' .fabro/` returns nothing.
- [ ] `fabro model list` resolves the default model: `spark::long-context` reports
      `default=true` and `small_default=true`, and `coders-a`/`coders-b` remain on
      `box-a`/`box-b`.
- [ ] `fabro model test long-context` passes (the small/utility model answers).
- [ ] A run completes a stage after the change, which proves the default model still
      resolves for a utility call.
- [ ] `fabro validate` reports the recorded baselines for all three packages, and
      `ops/test-task-gates.sh` passes.

## Dependencies

Blocked by task 03. Removing the provider before the hosted models move takes every
hosted stage down at once.

## Risk

3. Removing `coders` before `default_model` has a target breaks run-title generation
   silently, and nothing in this repository gates it. The mitigation is the
   `spark`/`long-context` `default_model` plus verification that the model resolves.
   A residual, accepted cost: the escape hatch now pins one box instead of
   load-balancing two, so an operator hand-fire must not race the scheduler on
   `box-a`.
