# Task 03: wire the claim into `backlog`, `ralph` and `pr-review`

Read `00-overview-and-contracts.md` first (D1, D5, D6, D14; C2, C4). **Invoke
`/fabro-workflow`**, then read `docs/agents/invariants-graph.md` *Agents and hooks* and
*Packages*, and `ops/check-graph-invariants.py` R10 (`R10_COPIES`).

## Goal

Every box stage of a coder loop runs `fabro-io box-claim` first, and the claim knows which box
the run holds.

## Steps

1. **The box file (C2).** In the `claim` node of `backlog`, `ralph` and `pr-review`, add one line
   near the start that writes `{{ inputs.coder_pool }}` to `/tmp/fabro/box`, using `echo`
   (R6: no `printf '%s\n'`).
   - Its failure must not fail the node: append `|| true`.
   - `claim` routes on stdout, so the write goes to the file only, and nothing goes to stdout.
   - `ralph`'s `claim` is an R10 copy of `backlog`'s. Make the identical edit in both, so R10
     still passes without a new recorded replacement.
2. **`pr-review`'s fires must pass `coder_pool`.** Today
   `.fabro/workflows/backlog/scripts/fire-pr-review.sh` sends only
   `inputs: { pr_number: $pr, auto_merge: $am }` (around line 356). A hand-fired `pr-review`
   therefore rides `coders-a` through the stylesheet's `default('coders-a')`, with no lease
   (ADR 0005 already notes this).
   - Add `coder_pool: "coders-a"` to those inputs, so the `claim` substitution has a value and
     the run's box is explicit. Update the script's comment and its gate test in
     `ops/gate_tests/` if one asserts the input shape.
   - Confirm there is no other path that fires `pr-review`:

     `grep -rn "pr-review" ops/scheduler/src ops/*.sh .fabro/workflows/*/scripts .fabro/workflows/*/workflow.fabro`

     Today only hand fires go through this script (*Facts*). If you find another path, stop
     and report it.
   - This script is **not** deployed by `deploy-scripts`: the host runs the copy on
     `origin/main` (*Facts*). The script change and the graph change are in this one commit, so
     they go live in the same push. Keep them in one commit.
3. **The hook (C4)** in each of the three `workflow.toml` files.
   - Order: after `io-stage`, so the stage identity is written before the wait.
   - A comment above it says what it does, why it is blocking, why it exits 0, and the 60 s
     bound against `stall_timeout` (60 m in `backlog`).
4. **Environment (C4).** Add `FABRO_BOX_HEALTH = "{{ vars.FABRO_BOX_HEALTH }}"` to each
   `[run.environment.env]`, and `FABRO_API_URL` and `FABRO_API_TOKEN` to `pr-review`'s, each with
   a comment.
   - An unset server variable fails the RunIntent, so no run is created (*Facts*). The first
     line of the commit body says: **`FABRO_BOX_HEALTH` must be provisioned on the host before
     this commit is pushed.**
   - In `ops/provision-server-state.sh`, add `provision_variable FABRO_BOX_HEALTH
     'coders-a=http://10.10.0.29:5300/health,coders-b=http://10.10.0.56:5300/health'` next to the
     existing variables, with a comment naming the three workflows that read it. Extend its test
     in `ops/gate_tests/test_ops_scripts.py` if that test lists the variables.
5. **Checker R15** (`ops/check-graph-invariants.py`). Add a constant
   `CODER_LOOP_PACKAGES = ["backlog", "ralph", "pr-review"]`.
   - Each of them has the `box-claim` hook exactly as C4 gives it, and its `claim` node writes
     `/tmp/fabro/box`.
   - **No other package** has a hook that runs `fabro-io box-claim`. A background run with the
     claim would cancel background runs on its own box, its own run included.
   - Add a passing and a failing fixture for each half.
6. **Gate tests** (`ops/gate_tests/`): a test that runs `backlog`'s `claim` script with a
   stubbed `gh` and asserts that `/tmp/fabro/box` holds the pool and that stdout is unchanged.
   Also run the `box-claim` hook's script with `fabro-io` absent and assert exit 0, as
   `test_tools.py` does for `git-guard`.

## Done when

- `make check` passes, including the gate tests.
- The node and edge counts of `backlog`, `ralph` and `pr-review` are unchanged; this adds only
  hooks and lines.
- Commit: `feat(workflows): coder loops claim their box before each box stage (more-local-inference 03)`.
