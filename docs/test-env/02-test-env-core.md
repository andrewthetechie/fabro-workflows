# 02. `fabro-io test-env`: parse, prepare, fingerprint

## Outcome
`fabro-io` has a `testenv` module: the C1 parser, the C3 fingerprint and stamp, a
`prepare` function, and the C2 subcommands `test-env` and `test-env --check`. Tasks 03 and
05 build on it.

## Why
`ci.sh` must stop carrying its own copy of the variables (D2). That needs a reader that is
strict enough to fail a typo, and a prepare step both `ci.sh` and the tools share.

## Change
1. `ops/fabro-io/src/testenv.rs` (invoke `ms-rust` first):
   - `TestEnv::load(root) -> Result<Option<TestEnv>, String>`: reads `.fabro/test.toml`
     and enforces every C1 rule (`deny_unknown_fields`, version, name patterns, timeouts
     in range, no `PATH` key). `None` means no file.
   - `fingerprint(root, &TestEnv) -> String`: C3. Glob matching stays inside the checkout
     and never follows a symlink out of it.
   - `prepare(root, &TestEnv, budget) -> Result<Prepared, PrepareError>`: runs each `run`
     entry with `sh -c` from `root`, `[env]` applied and `path` prepended. It tees output
     to `/tmp/fabro/test-env.log` (creating `/tmp/fabro`), stops at the first failure, and
     enforces the budget by killing the process group. It writes the stamp on success and
     deletes it on failure.
   - `exports(root, &TestEnv) -> String`: the `export NAME='value'` lines, with single
     quotes escaped as `'\''` and `PATH` last.
2. `cli.rs`: `test-env [--root DIR]` and `test-env --check [--root DIR]`, exactly C2.
   `--root` defaults to the git top level of the cwd.
3. Add a `toml` dependency if the crate has none. Bump the crate version (0.4.0).

## Acceptance
- `fabro-io test-env --check` passes on the C1 example and fails, naming the key, on each of:
  an unknown key, `version = 2`, `PATH` in `[env]`, a target named `Back_end`, and
  `timeout = 0`.
- `fabro-io test-env` with no file prints nothing and exits 0.
- A failing second entry exits non-zero. stderr names `prepare.run[1]` and its text, and
  there is no stamp.
- `bash -c 'set -euo pipefail; test_env=$(fabro-io test-env); eval "$test_env"; echo x'`
  does not print `x` when preparation fails.

## Tests
`cargo test` unit tests for each parse rule, the export quoting (a value containing `'`),
and fingerprint changes (an input file edited, `test.toml` edited, a different root, and a
file outside the globs edited: unchanged). An integration test runs `prepare` with
`run = ["true", "echo hi > marker", "false"]` and checks the marker, the log, the missing
stamp and the exit.

## Depends on
Nothing.
