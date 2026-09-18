# Thread `coder_pool` through both root stylesheets

## Tracer-Bullet Outcome
A run fired with `args.inputs.coder_pool = "coders-a"` provably does its coding
on `10.10.0.29`, and a run fired with no `coder_pool` at all still works, landing
on the load-balanced `coders` group exactly as today.

## User Story
As the operator, I want to choose which inference box a run uses at fire time so
that the scheduler can pin work to a free box — without a fabro settings change,
a server restart, or a graph edit per box.

## Description
The only per-run model-routing lever fabro offers. Node `model` attributes are
**not** templated, and `args.model` on the run intent loses to any explicit
stylesheet assignment — but the **root graph's `model_stylesheet` is a MiniJinja
template that receives `inputs`**, so the selection goes there.

Two files change, one line each in `backlog`, one in `pr-review`. The shared
`_shared/review-merge/` graph is **not** edited: it has no stylesheet of its own
(a stylesheet on an imported graph is ignored with an
`imported_model_stylesheet_ignored` warning), and its `ci_fix_t1` carries
`class="rebase"`, which matches the importing graph's root stylesheet by class.

## Context Pack
- Source decisions: overview decision 12 (box pinning is a LiteLLM model group
  plus a run input, not a fabro provider), resting on overview **finding 4** —
  "the stylesheet is the only per-run routing lever, and it is strict" — and
  finding 3 for why a provider per box is blocked.
- Repo facts: the current `backlog` stylesheet, verbatim, is in
  `.fabro/workflows/backlog/workflow.fabro` and already carries
  `reasoning_effort: medium` on the coder classes (commit `ce831c0`) — that must
  be preserved.
- Non-goals: changing which model any hosted class uses; touching
  `_shared/review-merge/review-merge.fabro`; changing `[run.model.fallbacks]`
  beyond adding the two new keys.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  `.fabro/workflows/backlog/workflow.fabro`,
  `.fabro/workflows/pr-review/workflow.fabro`,
  `.fabro/workflows/backlog/workflow.toml` (fallback keys),
  `.fabro/workflows/pr-review/workflow.toml` (fallback keys, if it has a
  `[run.model.fallbacks]` table — check before assuming).

- Interfaces and names — current `backlog` stylesheet, verbatim:

  ```
  .coder           { model: coders; reasoning_effort: medium; }
  .coder-t2        { model: glm-4.7; }
  .coder-t3        { model: kimi-for-coding; }
  .coder-t4        { model: glm-5.3; }
  .rebase          { model: coders; reasoning_effort: medium; }
  .rebase-t2       { model: glm-5.3; }
  ```

  Target — **only the two `coders` lines change**:

  ```
  .coder           { model: {{ inputs.coder_pool | default('coders') }}; reasoning_effort: medium; }
  .rebase          { model: {{ inputs.coder_pool | default('coders') }}; reasoning_effort: medium; }
  ```

  Current `pr-review` stylesheet, verbatim:

  ```
  *           { model: glm-5.3; }
  .review     { model: glm-5.3; }
  .rebase     { model: coders; reasoning_effort: medium; }
  .rebase-t2  { model: glm-5.3; }
  ```

  Target — the `.rebase` line only, same replacement.

- Verified external contracts (all verified live against
  `http://10.10.0.32:32276/api/v1` on 2026-09-18 by registering the real
  `backlog` package with each form):

  ```
  {{ inputs.coder_pool }}                          POST /workflow-versions -> 201
  {{ inputs.coder_pool | default('coders') }}      POST /workflow-versions -> 201   <-- use this
  {{ inputs.coder_pool | default("coders") }}      POST /workflow-versions -> 422
        detail: workflow graph `workflows/backlog/workflow.fabro` is invalid
  {{ inputs.coder_pool | default(\"coders\") }}     POST /workflow-versions -> 201
  ```

  **Single quotes are mandatory in practice.** An unescaped `"` terminates the
  enclosing DOT attribute string. `\"` also works and is the one backslash a
  `.fabro` file may contain, but single quotes avoid depending on that exemption.

  End-to-end with the single-quote form, same verification run:

  ```
  POST /runs  args.inputs = {}                          -> 201   (default fires)
  POST /runs  args.inputs = {"coder_pool":"coders-a"}   -> 201
  ```

  **The default is load-bearing.** The stylesheet renders strict at run time, so
  an unbound input does not render empty — it fails the run at compile with
  `422 run_compile_invalid`, the same failure an unbound `pr_number` produces.

  **Do not** bind `coder_pool` in `[run.inputs]` instead. `AGENTS.md` records that
  trap for `pr_number`: a bound default converts "no input supplied" into "one
  specific answer", which here would silently send every hand-fired run to one box.

- Behavior rules: `[run.model.fallbacks]` keys on the **requested** model name, so
  `coders-a` and `coders-b` need their own entries or a run pinned to a box has no
  fallback. Current table in `backlog/workflow.toml`, verbatim:

  ```toml
  [run.model.fallbacks]
  coders = ["litellm:glm-4.7"]
  "glm-4.7" = ["litellm:kimi-for-coding"]
  kimi-for-coding = ["litellm:glm-5.3"]
  "glm-5.3" = ["litellm:kimi-k3"]
  kimi-k3 = ["litellm:glm-5.3"]
  long-context = ["litellm:kimi-k3"]
  ```

  Add, preserving the operator's documented "z.ai before kimi" policy:

  ```toml
  "coders-a" = ["litellm:glm-4.7"]
  "coders-b" = ["litellm:glm-4.7"]
  ```

  **A key containing a dot must stay quoted** or the TOML parse turns it into a
  nested table and the automation fire returns 422 with nothing reported until
  then (`AGENTS.md`).

- Error and security rules: None new.

## Acceptance Criteria
- [ ] All three workflows validate in the container with the documented
      baselines and no new warnings; `MISMATCHES: 0`.
- [ ] `python3.11 -c 'import tomllib; tomllib.load(open(".fabro/workflows/backlog/workflow.toml","rb"))'`
      succeeds and shows `coders-a`/`coders-b` as **top-level string keys**, not
      nested tables.
- [ ] A run fired with `coder_pool="coders-a"` does its coder stage on `.29`:
      during that stage `curl http://10.10.0.29:8000/slots` reports
      `is_processing: true` and `.56` does not.
- [ ] A run fired with **no** `coder_pool` reaches its coder stage successfully.

## Test Expectations
No unit-test framework — these are DOT graphs. The validator is fabro's own, plus
a live pin check.

1. Container validation, exactly as `AGENTS.md` prescribes (rsync → docker cp →
   `fabro validate` for each of the three packages → `check-routing-schemas.py`).
   Expected: the recorded node/edge baselines, `Validation: OK`, `MISMATCHES: 0`,
   and only the one deliberate `pr_number` warning on `pr-review`.

2. Live pin check. Register the `.fabro`-rooted version, then:

   ```sh
   jq -n --arg v "$VERSION_ID" '{workflow_version_id:$v,
     target:{kind:"git",repo:"andrewthetechie/jelly-swipe",branch:"main"},
     args:{inputs:{coder_pool:"coders-a"}}, environment_id:"python"}'
   ```

   POST to `/runs` (expect `201`), `/runs/{id}/start` (expect `200`), then poll
   `http://10.10.0.29:8000/slots` during the `coder` stage. Expected literal:
   `is_processing` is `true` on `.29` and `false` on `.56`.

## Dependencies
- Blocked by: "Split the `coders` model group into `coders-a` and `coders-b`"
- Why blocked: the acceptance criteria assert a run lands on a named box, which
  requires those model groups to exist. The graph change itself is safe without
  them because of `default('coders')`.
- Blocks: "Lease state machine and dispatch loop"

## Labels
`feature`, `workflows/backlog`, `workflows/pr-review`, `priority:high`

## Estimate
Small

## Risk
3 - touches the model routing of every coder stage in both production workflows.
A malformed stylesheet fails every run at compile, immediately and loudly, which
is the good failure mode.

## Validator Stopping Point
Three workflows validate clean with the documented baselines, the TOML parses
with quoted dotted keys, and a pinned run is observed on the intended box.
