# 03. Parse the Task validation keys in `test.toml`

## Outcome
`fabro-io` parses C3: `[task]`, per-target `tests` or `watch`, and `[checks.*]`. It still
rejects unknown keys. `test-env --check` prints the ownership warnings.

## Why
D5. The repository declares the shape, and `deny_unknown_fields` means the keys have to be
known before any repository can use them (D13).

## Change
1. `ops/fabro-io/src/testenv.rs`:
   - The raw structs gain `task: Option<RawTask { full, skip }>` and a target's `tests` and `watch` (`Option<Vec<String>>`), plus `checks: BTreeMap<String, RawCheck>` (`about`, `run`, `watch`, `files`, `cwd`, `timeout`).
   - The validated `TestEnv` gains the same, in file order. Keep the order of targets and checks as written, because `task-plan` uses the first match. If the TOML crate's map type loses order, use its order-preserving feature.
   - Errors: `tests` and `watch` both set; an empty `watch` on a check; a check name not matching `^[a-z0-9-]+$`; a check `timeout` outside 1..=600; an invalid glob (compile it with the crate `prepare.inputs` uses).
   - Add a `TestEnv::declares_task_keys()` helper (C3).
   - Expose the built-in `full` glob list as a `const` slice next to it, with a comment pointing at C3.
2. `test-env --check`: after a successful parse, the three warnings in C3. Get the test files from `fabro-code list-tests`, run with `--root` as cwd. When the command is missing, print one skip line. The exit code is unchanged by warnings.
3. `ops/fabro-io/tests/testenv_tests.rs`, one test each:
   - every new key parsing
   - each new error
   - an unknown key inside `[task]` and inside `[checks.x]` rejected
   - file order kept
   - each warning, using a stub `fabro-code` on `PATH` as `code_tests.rs` does
   - the skip line when it is absent
4. `docs/test-env/setup-guide.md`: a "Task validation keys" section with C3's example and
   rules, and the two update rules. A new test file in a covered directory needs nothing; a
   new test directory or app needs a `tests` or `skip` entry, otherwise every task touching
   it runs the full suite.

## Acceptance
- `cd ops/fabro-io && cargo test` passes. `cargo clippy --all-targets -- -D warnings` is clean.
- All four target repositories' current `test.toml` still parse (fixtures copied from 00's
  facts, or fetched with `gh api`).

## Depends on
Nothing. It is released in task 05.
