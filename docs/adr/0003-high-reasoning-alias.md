# `high-reasoning` is a role alias with overflow, not a load balancer

**Status:** accepted (2026-09-16)

Both workflows name models directly in their stylesheets — `.decomp { model: glm-5.3; }`
and eight more like it — so every change of reasoning model is a commit to this
repository and a deploy to four automations. `issue-triage` introduces a LiteLLM model
named **`high-reasoning`**, and the workflows ask for the *role* rather than the model.
The routing decision moves to LiteLLM, where it can be changed without touching a graph.

`high-reasoning` resolves to **`glm-5.3`, with a router-level fallback to `kimi-k3`** on
rate-limit or error. It is deliberately **not** a two-member pool, although `coders`
(strix ×2) proves that shape works and LiteLLM would balance the two just as happily.

The rejected alternative is the more interesting one. glm-5.3 and kimi-k3 sit on
*different* plans — z.ai grants 3 concurrent sessions account-wide, the Kimi coding plan
is a separate budget — so a true weighted pool would genuinely widen the pinch point
ADR 0001 identifies, rather than renaming it. It was rejected because it silently
reverses the operator's quota policy recorded in `ops/README.md` and implemented by
both workflows' fallback chains: *z.ai before kimi*. A balanced pool spends Kimi quota
on every judgment stage of every run, including the ~24 triage runs a day that will
mostly quiet-exit, rather than only under z.ai pressure. Keeping Kimi as overflow keeps
that budget for the `coder-t3` role that was given it deliberately.

## Consequences

- **This is an indirection change, not a capacity change.** ADR 0001's finding stands
  unaltered: glm-5.3 remains the pinch point, and three concurrent runs can still all
  be in a glm-5.3 stage at once. Nothing here should be read as having relieved that.
- `issue-triage`'s `workflow.toml` deliberately carries **no** `[run.model.fallbacks]`
  entry for `high-reasoning`. LiteLLM owns the overflow; a second fallback chain in
  Fabro would race it and make the effective route unknowable from either side.
- `backlog` and `pr-review` keep their explicit model names and their circular
  `glm-5.3 → kimi-k3 → glm-5.3` fallbacks for now. Migrating them is one stylesheet
  line each and retires that circle — ADR 0001 named it as the first thing to revisit —
  but it is an untested routing change to the two graphs that open PRs and merge to
  `main`, so it lands as a follow-up commit that does nothing else.
- LiteLLM keeps models in Postgres, not in git: `manifests/apps/litellm/values.yaml`
  sets `model_list: []` with `STORE_MODEL_IN_DB: "True"`, and everything is created
  through the Admin UI. `high-reasoning` is therefore server state of exactly the kind
  `provision-server-state.sh` exists to make reproducible, and gets the same treatment.
  A rebuilt LiteLLM with no `high-reasoning` model fails every `issue-triage` run at
  its first agent stage.
- The name is a role, so the model behind it may change without a workflow edit. That
  is the point, and it is also the risk: a run's actual model is then only discoverable
  from LiteLLM, not from this repository. `fabro events -p` still records what was used.
