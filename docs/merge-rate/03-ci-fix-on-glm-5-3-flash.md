# `ci_fix_t1` runs on `glm-5.3-flash` through a new `ci-fix` class

## Tracer-Bullet Outcome
The first CI-fix tier of the merge phase runs on the hosted `glm-5.3-flash` model in both
`backlog` and `pr-review`, instead of on the run's local coder box, where 3 of its 12
visits hit the 20-minute timeout and blocked the merge.

## User Story
As the operator, I want the tier-1 CI fixer on a hosted model, so that it finishes inside
its 20-minute budget instead of timing out and leaving the merge blocked as "CI-fix
report is missing or invalid".

## Description
Add one stylesheet class, `ci-fix`, to both root graphs, and point `ci_fix_t1` at it:

1. `.fabro/workflows/_shared/review-merge/review-merge.fabro`: in node `ci_fix_t1`,
   change `class="rebase"` to `class="ci-fix"`, change the label, and add a comment.
2. `.fabro/workflows/backlog/workflow.fabro`: add a `.ci-fix` rule to `model_stylesheet`.
3. `.fabro/workflows/pr-review/workflow.fabro`: add the same `.ci-fix` rule, and fix the
   comment sentence that says `.rebase` covers `ci_fix_t1`.
4. `AGENTS.md`: add `ci-fix` to the list of classes that need a rule in both stylesheets.

## Context Pack
- Source decisions: overview decision 5. ADR 0011 D3.
- Repo facts:
  - The node today, verbatim (`review-merge.fabro`):
    ```
        ci_fix_t1 [label="Fix failing CI (tier 1: coders)", class="rebase", timeout="20m", prompt="@prompts/ci_fix.md.j2"]
        ci_fix_t2 [label="Fix failing CI (tier 2: glm-5.3)", class="rebase-t2", timeout="20m", prompt="@prompts/ci_fix.md.j2"]
    ```
  - `backlog`'s stylesheet today, verbatim. Its `*` rule is a **local box**, so a class
    with no rule silently runs there:
    ```
            model_stylesheet="
                *                 { model: coders-a; }
                .decomp           { model: glm-5.3; }
                .improve          { model: {{ inputs.coder_pool | default('coders-a') }}; reasoning_effort: medium; }
                .coder            { model: {{ inputs.coder_pool | default('coders-a') }}; reasoning_effort: medium; }
                .coder-t2         { model: glm-5.3-flash; }
                .coder-t3         { model: kimi-for-coding; }
                .coder-t4         { model: glm-5.3; }
                .rebase           { model: {{ inputs.coder_pool | default('coders-a') }}; reasoning_effort: medium; }
                .rebase-t2        { model: glm-5.3; }
                .review-task      { model: glm-5.3; }
                .review-standards { model: glm-5.3-flash; }
                .review-quality   { model: glm-5.3; }
                .review-frontier  { model: kimi-k3; }
                .merge-standards  { model: glm-5.3-flash; }
                .merge-spec       { model: glm-5.3; }
                .merge-fix        { model: glm-5.3; }
            "
    ```
  - `pr-review`'s stylesheet today, verbatim:
    ```
            model_stylesheet="
                *                 { model: glm-5.3; }
                .rebase           { model: {{ inputs.coder_pool | default('coders-a') }}; reasoning_effort: medium; }
                .rebase-t2        { model: glm-5.3; }
                .merge-standards  { model: glm-5.3-flash; }
                .merge-spec       { model: glm-5.3; }
                .merge-fix        { model: glm-5.3; }
            "
    ```
  - `.rebase` stays in both stylesheets. `pr-review`'s `rebase_agent_t1` still uses it.
    In `backlog` it will match no node after this change. Leave it; an unused rule is
    harmless.
  - `glm-5.3-flash` is already in the server's model catalog (`.merge-standards` and
    `.coder-t2` use it). It is reasoning-capable, which matters because `AGENTS.md` bans
    `glm-4.7` for lacking that.
  - The model-escalation Discord hook matches
    `(^|[.])(rework_t[2-4]|resolve_merge|ci_fix_t2)$`. Do **not** add `ci_fix_t1` to it.
    That hook reports a stage *leaving* the local box as an escalation, and tier 1 is
    now hosted by design, not an escalation.
- Non-goals: do not change `ci_fix_t2`, any timeout, the prompt
  `_shared/review-merge/prompts/ci_fix.md.j2`, or any hook.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  .fabro/workflows/_shared/review-merge/review-merge.fabro
  .fabro/workflows/backlog/workflow.fabro
  .fabro/workflows/pr-review/workflow.fabro
  AGENTS.md
  ```
- **Edit 1: `review-merge.fabro`.** Replace
  ```
      ci_fix_t1 [label="Fix failing CI (tier 1: coders)", class="rebase", timeout="20m", prompt="@prompts/ci_fix.md.j2"]
  ```
  with
  ```
      // ci_fix_t1 runs on the hosted `glm-5.3-flash` through class `ci-fix` (ADR 0011 D3).
      // On the run's local coder box it had a p50 of 14 minutes, and 3 of 12 visits died at
      // this 20m timeout, leaving no ci_fix_result.json, so the merge was refused. The 20m
      // is now UNMEASURED for flash: it is kept only because the 60-minute merge budget
      // assumes one 20m attempt per tier. Re-time it from the first ten flash visits.
      // Both importing stylesheets (backlog, pr-review) must carry a `.ci-fix` rule.
      ci_fix_t1 [label="Fix failing CI (tier 1: glm-5.3-flash)", class="ci-fix", timeout="20m", prompt="@prompts/ci_fix.md.j2"]
  ```
- **Edit 2: `backlog/workflow.fabro`.** Directly after the line
  `            .merge-fix        { model: glm-5.3; }` in `model_stylesheet`, add
  ```
              .ci-fix           { model: glm-5.3-flash; }
  ```
  Match the existing indentation (12 spaces) and column alignment.
- **Edit 3: `pr-review/workflow.fabro`.**
  - Directly after `            .merge-fix        { model: glm-5.3; }`, add the same line:
    ```
                .ci-fix           { model: glm-5.3-flash; }
    ```
  - In the `//` comment above the stylesheet, replace
    ```
                // "coders-a"` to lease one llama.cpp box for the whole run. `.rebase` also
                // covers the imported review-merge graph's `ci_fix_t1`, which carries
                // class="rebase" and has no stylesheet of its own.
    ```
    with
    ```
                // "coders-a"` to lease one llama.cpp box for the whole run. The imported
                // review-merge graph's `ci_fix_t1` used to share `.rebase`; since ADR 0011 D3
                // it carries class="ci-fix" and runs on glm-5.3-flash in both graphs.
    ```
- **Edit 4: `AGENTS.md`.** In the invariant row that starts
  `` | A class used by a node in `_shared/review-merge/` has an explicit rule ``, replace
  ```
  Six classes carry this today (`merge-standards`, `merge-spec`, `merge-fix`, `rebase`, `rebase-t2`)
  ```
  with
  ```
  Six classes carry this today (`merge-standards`, `merge-spec`, `merge-fix`, `ci-fix`, `rebase-t2`, and `rebase`, which only `pr-review`'s own rebase agent still uses)
  ```
- Interfaces and names: class name `ci-fix`, which follows the `[a-z0-9-]` rule. Model
  id `glm-5.3-flash`, exactly as the existing rules spell it.
- Verified external contracts: stylesheet rule syntax
  `.class { model: <id>; }` is copied from the existing rules above.
- Behavior rules: none beyond the model change.
- Error and security rules: None.

## Acceptance Criteria
- [ ] `ci_fix_t1` carries `class="ci-fix"` and the label
      `Fix failing CI (tier 1: glm-5.3-flash)`.
- [ ] Both stylesheets contain `.ci-fix           { model: glm-5.3-flash; }`.
- [ ] The check below prints `ok`.
- [ ] `./ops/test-task-gates.sh` still passes, with no new checks. It does not read
      stylesheets.

## Test Expectations
- There is no offline harness for stylesheets, so the check is a structural assertion.
  Run it from the repository root with python3 (standard library only):
  ```sh
  python3 - <<'EOF'
  import re
  shared = open(".fabro/workflows/_shared/review-merge/review-merge.fabro").read()
  assert re.search(r'ci_fix_t1 \[[^\n]*class="ci-fix"', shared), "ci_fix_t1 class"
  for g in (".fabro/workflows/backlog/workflow.fabro", ".fabro/workflows/pr-review/workflow.fabro"):
      s = open(g).read()
      assert re.search(r'\.ci-fix\s+\{ model: glm-5\.3-flash; \}', s), g
  print("ok")
  EOF
  ```
  Expected output: `ok`.
- Task 11 (operator) confirms the resolution on the server with `fabro validate`, and
  optionally `fabro preflight`, whose summary names the model a class resolved to.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: 11 (validate)

## Labels
`enhancement`, `review-merge`, `priority:high`

## Estimate
Small

## Risk
2 - A class missing from one stylesheet would silently fall back to `*`. The structural
check guards against that. The model change itself is reversible in one line.

## Validator Stopping Point
The python check prints `ok`, and `./ops/test-task-gates.sh` passes.
