# 05. `fabro-io task-check`, the `task_check` tool, `fabro-test --task`; release 0.5.0

## Outcome
`task-check` runs a plan (C5). Agents can call it as `task_check` (C6) or `fabro-test --task`.
`fabro-io` is 0.5.0.

## Why
D9, D11 and P3. The gate and the agent run one implementation, with one difference: the gate
always prepares fresh, and the agent uses the cache.

## Change
1. A new module, `ops/fabro-io/src/taskcheck.rs`:
   - Plan with `taskplan`. In scoped mode run the checks, then prepare (fresh with `--gate`; otherwise by fingerprint, as `runtests` does), then run the targets.
   - Reuse `runtests`' command building and process-group timeout handling. Move shared code into a function rather than copying it.
   - Hold `EnvLock` for the whole call.
   - A check gets `testenv::command_env` plus `FABRO_TASK_BASE`, and never prepares.
   - Write `/tmp/fabro/task_check.log` (every item's full output, with headers) and the C5 result. Exit codes per C5.
   - Without `--gate`, apply the 600 s total budget and report what did not fit.
2. `cli.rs`: dispatch `task-check`.
3. `serve.rs`: the `task_check` tool (C6), listed wherever `run_tests` is. It renders C5 compactly; no full log in the tool result.
4. `ops/profile-images/fabro-test`: if the first argument is `--task`, run `exec fabro-io task-check "$@"` with `--task` removed. Otherwise do as today.
5. `Cargo.toml`: version 0.5.0. Update `ops/fabro-io/README.md`.
6. Tests in `ops/fabro-io/tests/taskcheck_tests.rs`, plus `serve_tests.rs`:
   - green, red, and red-then-green items (P3 runs them all)
   - a preparation failure (the targets are not run, the checks are)
   - `--gate` always preparing, against a counter in the `prepare.run` fixture
   - the budget cut
   - `mode: full` running nothing
   - the tool listed with its schema
   - `fabro-test --task` reaching `task-check` (a shell test, or a check in `runtests_tests.rs` style)

## Acceptance
- `cargo test` and clippy are clean, and `make check` passes.
- In the task 02 container on womens-fantasy-sports, with the draft task 09 keys and one backend route edited:
  - `fabro-test --task` prepares, runs `prek` and the selected backend tests, and prints `ok`
  - a second call does not prepare again; with `--gate` it does
- Record the output in `02-evidence.md`.

## Depends on
04.
