# 03. `run_tests`, `fabro-test`, and `baseline_check` targets

## Outcome
Every manifest stage is offered `run_tests` (C4). `fabro-io run-tests` gives the same
behaviour from the shell. `baseline_check` accepts a target and prepares from the base tree
(C5).

## Why
D3 to D6. The coder in `01M4HVKKAHPFK3X9KJ18D8YDZG` needed one call that runs
`tests/seed/test_load.py` against a seeded Postgres, and had no such call.

## Change
1. `ops/fabro-io/src/runtests.rs`:
   - the argument check (C4: plain, `flags`, `value_flags`, reject),
   - the prepare-if-needed decision (`fresh`, no stamp, fingerprint mismatch),
   - the budget arithmetic, the run (`sh -c "<run> <quoted args>"` in `cwd`), and the
     C4 result text.
   Reuse `gitsafe`'s process-group timeout and tail code rather than copying it.
2. `serve.rs`: build `run_tests`'s description and `target` enum from the checkout's
   `test.toml` at `list_tools` time. Add it wherever the `gitsafe` tools are added.
3. `cli.rs`: `run-tests TARGET [args…] [--fresh]`.
4. `gitsafe.rs` `baseline_check`: an optional `target` + `args` (mutually exclusive with
   `command`). Before running, either way, load the base worktree's `test.toml` (falling
   back to the working tree's) and prepare from the base worktree, which writes its stamp.
   The worktree's dependency-directory handling (docs/coder-tweaks 04) is unchanged.

## Acceptance
- On a fixture checkout with a two-target `test.toml`, `list_tools` shows `run_tests`
  with both names and `about` texts in its description.
- `run_tests backend ["-n0"]` is rejected before anything runs, naming `-x`, `-v`, `-k`.
  `["-k", "careers"]` is accepted.
- First call: the result reports `prepared in Ns`. Second call: no preparation. After an
  input file is edited: preparation again. `fresh: true`: preparation again.
- After `baseline_check` with a target, the next `run_tests` prepares again.
- A preparation that exceeds its timeout reports `phase: prepare` and a tail. The call
  returns before 600 s.

## Tests
`cargo test` against a temporary git repository whose `test.toml` targets are shell
commands that write marker files (no Postgres). Cover each acceptance line, plus a missing
and an invalid `test.toml`.

## Depends on
02.
