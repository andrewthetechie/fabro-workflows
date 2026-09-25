# C1 · Reproduction gate and change-scoped tests

**Status:** proposed (rev 2 P1-8 and P1-9, merged). **Axis:** quality, throughput.
**Effort:** M–L. **Feasibility:** high. **Depends on:** a task `kind` field (below), which
A3 and B2 also want.

## Problem

- **A bug fix can ship with evidence that proves nothing.** A test that also passes on the
  unfixed code does not show the fix works. no_human refused 46 bug-fix "proofs" this way,
  with a mechanical check: *the tests offered as evidence must fail at the merge base and pass
  on the new tree.*
- **Every task pays the full suite.** Per-task `validate` runs `./.fabro/setup.sh &&
  ./.fabro/ci.sh` on a 20-minute timeout, and the merge phase runs it again. A multi-task run
  pays the full suite, plus setup, once per task.

## Design

### The task `kind` field (the enabling contract change)

Add `kind ∈ {bugfix, feature, refactor, test, docs}` to each entry in `tasks.json`.
`decompose.md.j2` and `extra_decompose.md.j2` emit it, and `decompose_gate`/`extra_gate`
validate it (or B3's `fabro-submit task --kind` does). This is a **task-queue contract
change**, the category `AGENTS.md` singles out, because a cursor error there silently skips a
task. So `ops/test-task-gates.sh` grows in the same commit.

### Scoped tests: `.fabro/test-routes.toml` per target repo

```toml
[[route]]
match   = ["frontend/**"]
command = "cd frontend && npm test -- --run"
[[route]]
match   = ["backend/app/scoring/**", "backend/tests/scoring/**"]
command = "uv run pytest backend/tests/scoring -q"
```

The rule (from no_human's `test_commands`): a route applies only when **every** changed file
matches it. Otherwise run the full `ci.sh`. Repo-wide guards (lint, typecheck) stay in every
route. Per-task `validate` uses the route. The merge-phase `validate` **always** runs the
full `ci.sh`.

### The reproduction gate

For a `bugfix` task, after `validate` passes:

1. Find the test files the task changed or added (`git diff --name-only $task_base_sha HEAD`
   filtered by test globs).
2. Stash the non-test changes: `git stash push -- <non-test files>`, or check out
   `$task_base_sha -- <non-test files>`.
3. Run the scoped test command. It **must fail**.
4. Restore the fix and run it again. It **must pass**.
5. Write `repro.json`. A failure routes to the rework ladder with the message *"your test
   passes without your fix; it does not reproduce the bug."*

`task_base_sha` already exists (`next_task` writes it).

## Verification

Real-git fixtures in `ops/test-task-gates.sh`: a fix whose test fails before and passes after
(gate passes), a test that passes on both (gate fails), and a `feature` task (gate skipped).

## Open questions

1. Flaky tests make step 3 lie. Run step 3 twice and require both runs to fail.
2. Some bugs cannot be unit-reproduced (UI, infra). Allow `kind=bugfix` with
   `repro: manual` only when triage recorded that decision, visible on the issue.
