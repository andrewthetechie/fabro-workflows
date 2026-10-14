# 04. `fabro-io task-plan`

## Outcome
`fabro-io task-plan [--base SHA] [--root DIR]` prints the C4 plan for the current diff.

## Why
D3, D4, D6, D7, D8, P1 and P2. This is how the gate decides what to run: the selection, its
ownership mapping and every fallback, in code that cargo tests can cover.

## Change
1. A new module, `ops/fabro-io/src/taskplan.rs`, holding:
   - `changed_set(root, base)` (C1)
   - `plan(root, env, changed, code_answer) -> Plan`, which is pure: no process, no git
   - a thin `run_cli` that gathers the inputs, calls `fabro-code tests --json` (cwd `root`, a 60 s timeout; reuse `code.rs`'s checkout and timeout handling), and prints the JSON

   Keep `plan` pure, so the rules are tested without git or a code index.
2. Implement the C4 steps in order. The first fallback that applies wins. The `reason`
   strings are exactly C4's: `no_config`, `invalid_config`, `full_glob:<path>`,
   `deleted:<path>`, `index_unavailable`, `unowned_test:<path>` and `path_outside_cwd:<path>`.
3. `cli.rs`: dispatch `task-plan`, and add it to the help text.
4. `ops/fabro-io/tests/taskplan_tests.rs`. Pure tests over `plan`:
   - no config
   - a declared `full` glob, and a built-in one (`backend/uv.lock`, `frontend/package.json`, `backend/tests/conftest.py`)
   - a deleted source file, and a deleted test file (the test file does not escalate)
   - the index unavailable
   - an unowned test
   - a test in `skip` (dropped, no escalation)
   - a path outside `cwd`
   - a target with 201 selected tests runs whole
   - a `watch` target
   - a check with `files = true` and a deleted file
   - `untested` and `unchecked`
   - the conftest rule, with `why` reading `<seed> via <conftest>`
   - first-declared-target ownership

   `changed_set` gets a real-git test: an uncommitted edit, an untracked file, and a rename showing its old path.
5. A womens-fantasy-sports-shaped fixture: a `test.toml` with the task 09 keys and a canned
   `fabro-code` answer. The plan must equal a checked-in expected JSON.

## Acceptance
- `cargo test` and clippy are clean.
- In the task 02 container, with a womens-fantasy-sports clone and its draft task 09 `test.toml` (not committed there):
  - touching one backend route file gives `mode: scoped` with a `backend` target and file args relative to `backend/`
  - touching `backend/uv.lock` gives `full_glob:backend/uv.lock`
- Record both in `02-evidence.md`.

## Depends on
02 (C2's shape) and 03.
