# Task 03: wire the claim into `backlog`, `ralph` and `pr-review`

Read `00-overview-and-contracts.md` first (D1, D5, D14; C2, C4). **Invoke `/fabro-workflow`**,
then read `docs/agents/invariants-graph.md` *Agents and hooks* and *Packages*.

## Goal

Every box stage of a coder loop runs `fabro-io box-claim` first, and the claim knows which box
the run holds.

## Steps

1. **The box file (C2).** In the `claim` node of `backlog`, `ralph` and `pr-review`, add one line
   near the start that writes `{{ inputs.coder_pool }}` to `/tmp/fabro/box`, using `echo`
   (R6: no `printf '%s\n'`).
   - Its failure must not fail the node: append `|| true`.
   - `claim` routes on stdout, so the write goes to the file only, and nothing goes to stdout.
2. **`pr-review`'s fires must pass `coder_pool`.** Today
   `.fabro/workflows/backlog/scripts/fire-pr-review.sh` sends only
   `inputs: { pr_number: $pr, auto_merge: $am }` (around line 356). A hand-fired `pr-review`
   therefore rides `coders-a` through the stylesheet's `default('coders-a')`, with no lease
   (ADR 0005 already notes this).
   - Add `coder_pool: "coders-a"` to those inputs, so the `claim` substitution has a value and
     the run's box is explicit.
   - Find every other path that fires `pr-review`:

     `grep -rn "pr-review" ops/scheduler/src ops/*.sh .fabro/workflows/*/scripts`

     List any path that cannot pass the input in the commit and in task 12's runbook.
   - Changing `fire-pr-review.sh` is a deploy (`deploy-scripts`). That is the operator's step
     in task 12, and it must land **before** this task's graph change reaches `main`.
     Otherwise a hand fire fails on the unbound input.
3. **The hook (C4)** in each of the three `workflow.toml` files.
   - Order: after `io-stage`, so the stage identity is written before the wait.
   - A comment above it says what it does, why it is blocking, why it exits 0, and the 90 s
     bound against `stall_timeout` (60 m in `backlog`).
4. **Environment (C4).** Add `FABRO_BOX_HEALTH = "{{ vars.FABRO_BOX_HEALTH }}"` to each
   `[run.environment.env]`, and `FABRO_API_URL` and `FABRO_API_TOKEN` to `pr-review`'s.
   - A missing server variable must not fail a run. Check how `{{ vars.X }}` behaves when unset
     by reading how `FABRO_AUTO_MERGE` is handled (`backlog/workflow.toml` comments).
   - If an unset variable fails the run, the operator's task 12 sets the variable **before**
     this task deploys, and the commit says so in its first line of body text.
5. **Checker R15** (`ops/check-graph-invariants.py`): every package whose manifest has a
   `true` or `"plan-arm"` box stage has the `box-claim` hook exactly as C4 gives it, and its
   `claim` node writes `/tmp/fabro/box`. Add a passing and a failing fixture.
6. **Gate tests** (`ops/gate_tests/`): a test that runs `backlog`'s `claim` script with a
   stubbed `gh` and asserts that `/tmp/fabro/box` holds the pool and that stdout is unchanged.

## Done when

- `make check` passes, including the gate tests.
- The node and edge counts of `backlog`, `ralph` and `pr-review` are unchanged; this adds only
  hooks and lines.
- Commit: `feat(workflows): coder loops claim their box before each box stage (more-local-inference 03)`.
