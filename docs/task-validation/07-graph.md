# 07. The Task validation node, and the merge-phase skip

## Outcome
`backlog`'s `validate` is Task validation (C7). `open_pr_prep` records the tree it passed,
and the merge phase's `validate` skips that tree (C8). `ops/test-task-gates.sh` covers both.
No node or edge is added or removed.

## Why
D1, D2, D6 and D9. This is the change that moves the time. Pushing it is a deploy, and it
is safe to push before any repository opts in. With no task keys, `task-plan` returns
`no_config` and the node runs `setup.sh && ci.sh` exactly as today. The same full run
happens when the image is old (`task_check_error`).

## Change
Invoke `/fabro-workflow` first. Read `docs/agents/invariants-graph.md`: routing nodes,
`\"` as the only backslash, `#` only inside strings, POSIX `sh`, and resetting per-iteration
keys.

1. `.fabro/workflows/backlog/workflow.fabro`, `validate`:
   - Replace the script with C7: label "Task validation", `output_schema="routing"`, `timeout="20m"`.
   - Use `jq` to read `task_check.json` and `current_task.json`. Use `tr -d '{}'` on every line of log or summary that reaches stdout.
   - Build the scoped rework file with `jq -r` over `.results[] | select(.exit_code != 0)`. Cap it with `head -c 6000` on the tail section.
   - Keep the full branch's rework header byte for byte (`rework.md.j2` and the rescue paths match it).
   - Put a comment block above the node in the house style: what it runs, why it is scoped (ADR 0025), the fallback reasons, why a red result exits 0 and routes by context, and why the placeholder `rework.md` is written first.
2. The `validate -> review` edge condition becomes
   `outcome=succeeded && context.task_validation_ok=true`. The unconditional
   `validate -> rework_router` stays.
3. `open_pr_prep`: C8's two lines. Add `final_validated_tree` to the `rm -f` at the top, and
   write it right after the loop when `RC=0`. Extend the node's comment by one sentence
   saying who reads the file.
4. `.fabro/workflows/_shared/review-merge/review-merge.fabro`, `validate`: C8's skip, at the
   top of the script and after the placeholder `needs_human_reason` line. Add a comment
   saying it only ever fires in `backlog` (the only writer of the file), and why it never
   deletes the file.
5. Search every `.fabro` file and prompt for `Validate (setup + ci)`, `validate_output.log` and
   the rework header. Update any text that describes the per-task node as always running
   `ci.sh`. `rescue_brief` and `human_rescue` read `rework.md` and `validate_output.log`.
   Check that the scoped shape renders sensibly there.
6. `ops/test-task-gates.sh`, a new section for each:
   - **Scoped green:** a stub `fabro-io` writes a canned scoped `ok:true` result. Assert `task_validation_ok:true` and `mode=scoped`, that the stub `setup.sh` and `ci.sh` did not run (marker files), and that stdout holds exactly one brace-balanced object.
   - **Scoped red:** the rework file starts `## Validation failed (exit 1): task validation` and lists the failing check and target. Assert `task_validation_ok:false`, and exit 0.
   - **`mode: full` (`no_config`):** stub `setup.sh` and `ci.sh` run. Green and red variants, with the red rework header identical to today's.
   - **`final_fix: true`:** `fabro-io` is not called at all, and the full suite runs, `reason=final_fix`.
   - **`fabro-io` without `task-check`** (a stub that exits 1 with no file): full, `reason=task_check_error`.
   - **The placeholder:** a pre-existing `rework.md` is replaced before anything runs. Check it by making the stub `fabro-io` read the file.
   - **The parsed edges:** `validate->review outcome=succeeded && context.task_validation_ok=true`, and the unconditional `validate->rework_router`.
   - **The merge skip, with real git:**
     - `final_validated_tree` matching HEAD's tree: no suite runs, `ci_ok:true`, `fix_attempts` unchanged
     - a new commit after it: the suite runs
     - a dirty worktree: the suite runs
     - no file: the suite runs
   - **`open_pr_prep`:** `final_validated_tree` written on green, absent on red. Extend the existing `opp_run` fixtures.
7. `make check` (the routing checker covers R8 for the new `output_schema`), then
   `make check-host`. The baselines stay `Backlog (82 nodes, 210 edges)`, and the
   `PrReview` and `Dependabot` counts are unchanged.

## Acceptance
- `make check` and `make check-host` pass with the baselines unchanged.
- After the push, the first `backlog` run on any repository shows in `fabro events <run> -p`:
  - `validate` publishing `task_validation_mode: full, reason: no_config`, and `validate -> review` selected
  - `review_merge.validate` printing `skipped` when `review_fix` made no change

## Depends on
06. Push it together with 08.
