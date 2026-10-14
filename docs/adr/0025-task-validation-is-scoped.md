# Task validation is scoped to the diff, and Final validation is the net

**Status:** proposed (2026-10-14). It becomes accepted when the womens-fantasy-sports trial
(`docs/task-validation/` task 10) meets both targets of issue #26. The task series is
`docs/task-validation/`. It amends one consequence of ADR 0018 (below).

`backlog`'s per-task `validate` ran the repository's full `setup.sh && ci.sh` after every
coder and rework round. From 2026-09-25 to 2026-10-11 that was 10% of all stage time (72 h),
about 24 minutes a run over a median of 5 visits, and 11% of all lease time with the coder
box idle. Each run then validated its whole branch at least twice more: once in
`open_pr_prep`, and once in the merge phase's `validate`, which re-ran on an unchanged tree
whenever `review_fix` made no commit.

We decided that **the per-task gate, Task validation, runs only the repository's checks and
the tests that cover the files the task changed**, and that **Final validation in
`open_pr_prep`, which runs the full suite once on the merged branch, is the net** for
whatever Task validation did not select. Its existing route (#35: one `final_fix` task, then
`rescue` or `draft`) is unchanged.

- **The gate selects, never the agent.** `fabro-io task-plan` derives the selection from the
  task's diff: the changed test files, the tests the Code index says import the changed
  files, and every test under a `conftest.py` the import walk reaches. It maps each test to
  the Test target that owns it. An agent's opinion of which tests cover its change is what
  the gate exists to check, so the agent can neither add to nor remove from the selection.
- **The repository declares the shape, in `.fabro/test.toml`.** It says which test files each
  target owns, which paths force the full suite, which test files no target runs per task,
  and its lint and typecheck checks. The declaration describes directories and tools, not
  files, so adding a source or test file needs no edit.
- **Anything unfamiliar falls back to the full suite.** These cases fall back: a dependency
  manifest, `.fabro/**`, a `conftest.py` or a test config changed; the Code index is
  unavailable; a selected test has no owner; the repository declares nothing; or the task is
  a `final_fix`. A stale declaration costs time, never a missed test.
- **Agents can run the same plan.** `task_check` (shell: `fabro-test --task`) runs the plan
  against the current diff, so the coder runs before it finishes what the gate will run.
- **The merge phase skips a tree Final validation already passed.**

## Amendment to ADR 0018

ADR 0018 says that "`validate` always prepares from scratch". Task validation keeps that
guarantee. It prepares the Test environment fresh once per visit, then runs every selected
target against that one preparation. Only agent calls, `run_tests` and `task_check`, use the
cache.

## Considered options

- **The agent names its tests** in a submit contract, or adds them to the gate's selection.
  Rejected: a weak model picks the tests that pass, and the gate would then certify the
  agent's own judgment.
- **A repository script, `.fabro/task-check.sh CHANGED_FILES…`.** Rejected for the reasons
  ADR 0018 rejected `test-env.sh`. Every repository would rewrite the selection in shell, and
  no tool could check it or reuse the Test environment.
- **Escalate an untested source file to the full suite.** Rejected: many source files have
  no importing test, and every such task would pay the full suite. Final validation runs
  those files' indirect tests anyway.
- **Ignore a selected test that no target owns.** Rejected: a forgotten `test.toml` edit
  would then silently drop tests from the gate. Escalating makes the drift visible in the
  log and costs only time.
- **A parity A/B against the full gate.** Rejected: the plan trial (#25) already splits runs
  by issue parity. The rollout is per repository instead. A repository gets Task validation
  only when its `test.toml` declares the keys, and deleting them rolls it back.

## Consequences

- More failures surface at Final validation, which has a budget of one `final_fix`. The trial
  measures red final validations per run, and a rise of more than 5 points fails it.
- `test.toml` grows a second job. Its new keys are optional and strictly parsed, so
  `fabro-io` 0.5.0 must be in the images before any repository uses them. An edit still
  trips `config_changed`, so a human merges every change to what the gate runs.
- The Code index becomes load-bearing for validation, not just for reading. Its
  unavailability is a fallback to the full suite, never a pass.
