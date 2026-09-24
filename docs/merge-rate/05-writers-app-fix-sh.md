# writers-app: add `.fabro/fix.sh` (cargo fmt, clippy --fix, eslint --fix)

> **Work in the `andrewthetechie/writers-app` repository**, not in fabro-workflows. Open
> a normal PR there against `main`.

## Tracer-Bullet Outcome
writers-app gains a `.fabro/fix.sh` that applies `cargo fmt`, the machine-applicable
clippy fixes, and `eslint --fix`. Once fabro-workflows task 04 (the `autofix` node) is
deployed, every writers-app `backlog` task is auto-formatted before it is validated, so
formatting and auto-fixable lints stop failing `validate`.

## User Story
As the operator, I want writers-app's own formatters applied to agent changes before
validation, so that a run stops spending a rework stage on `cargo fmt` diffs and
auto-fixable clippy lints.

## Description
Add one new executable file, `.fabro/fix.sh`, at the writers-app repository root. It
runs three fixers, keeps going when one fails, and always exits 0. Nothing else in
writers-app changes.

## Context Pack
- Source decisions: fabro-workflows `docs/merge-rate/00-overview-and-contracts.md`
  decision 9 and contract C4, restated here:
  - fabro runs `./.fabro/fix.sh` from the repository root after every coder stage. If
    the file is not executable, fabro runs `bash ./.fabro/fix.sh`.
  - It may contain only formatters and machine-applicable lint fixes, never a step that
    changes behaviour or generates code.
  - fabro ignores its exit status. `.fabro/ci.sh` is the gate.
- Evidence: in fabro's run history, 31 of writers-app's 154 validation runs failed, mostly
  on `cargo fmt` "Diff in …" output and clippy lints (`item in documentation is missing
  backticks`, `using .clone() on a ref-counted pointer`).
- Repo facts (writers-app):
  - `.fabro/ci.sh` today, verbatim. `fix.sh` must fix what these checks enforce, and
    nothing more:
    ```bash
    #!/usr/bin/env bash
    set -euo pipefail
    # Validation mirrors .github/workflows/ci.yml (frontend + rust). Exit non-zero on failure.
    npm run typecheck
    npm run lint
    npm run test:unit
    npm run build
    cargo fmt --all -- --check
    cargo clippy --all-targets --all-features -- -D warnings
    cargo test --all-features
    cargo audit
    ```
  - The root `Cargo.toml` is a workspace (`[workspace] members = ["src-tauri"]`), so
    `cargo fmt --all` and `cargo clippy` run from the repository root.
  - `package.json` scripts: `"lint": "eslint ."`, and
    `"format": "cargo fmt --manifest-path src-tauri/Cargo.toml"`. The ESLint config is
    `eslint.config.mjs` at the root.
  - `.fabro/` holds `ci.sh` and `setup.sh` today.
- Non-goals: no Prettier, no `npm run format` (it only runs cargo fmt on one manifest),
  no `cargo audit fix`, no `bindings:generate`, no change to `ci.sh`.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft (writers-app `main`)

## Implementation Contract
- Expected files: `.fabro/fix.sh` (new, mode 755) in writers-app.
- Exact content:
  ```bash
  #!/usr/bin/env bash
  # Formatters and machine-applicable lint fixes ONLY. fabro's `autofix` stage runs
  # this after every coder or rework stage, before .fabro/ci.sh validates the change
  # (fabro-workflows ADR 0011 D5). Every step runs even if an earlier one fails, and
  # fabro ignores the exit status: ci.sh is the gate, not this file.
  # Never add a step here that can change behaviour or generate code.
  set -u
  cargo fmt --all || true
  cargo clippy --fix --allow-dirty --allow-staged --all-targets --all-features || true
  # clippy's fixes are not always rustfmt-clean, so format once more.
  cargo fmt --all || true
  npm run lint -- --fix || true
  exit 0
  ```
- Make it executable, and make sure git records the mode:
  `chmod +x .fabro/fix.sh && git add .fabro/fix.sh`, then confirm with
  `git ls-files -s .fabro/fix.sh`, which must start with `100755`.
- Interfaces and names: the path `.fabro/fix.sh` is fixed by the fabro contract.
- Verified external contracts: `cargo clippy --fix` needs `--allow-dirty` and
  `--allow-staged` to run in a working tree with uncommitted or staged changes, which is
  the normal state when fabro runs it. `npm run lint -- --fix` passes `--fix` through to
  `eslint .`.
- Behavior rules: on a clean, CI-green `main` checkout, running it must change **no**
  files.
- Error and security rules: None.

## Acceptance Criteria
- [ ] `.fabro/fix.sh` exists with the content above, and `git ls-files -s` shows mode
      `100755`.
- [ ] On a clean checkout of `main`, after `./.fabro/setup.sh`: `./.fabro/fix.sh` exits
      0, and `git status --porcelain` prints nothing.
- [ ] Break the formatting of one Rust file (for example, join two lines of a function
      in `src-tauri/src/lib.rs` onto one line), run `./.fabro/fix.sh`, and
      `cargo fmt --all -- --check` then exits 0.
- [ ] `./.fabro/ci.sh` still passes on the branch.

## Test Expectations
- There is no unit-test framework for this file. The checks are the commands above, run
  in the writers-app checkout:
  1. `./.fabro/fix.sh; echo "rc=$?"; git status --porcelain` → `rc=0`, then no lines.
  2. After deliberately unformatting one `.rs` file:
     `./.fabro/fix.sh >/dev/null 2>&1; cargo fmt --all -- --check; echo "rc=$?"` → `rc=0`.
     Revert the file afterwards with `git checkout -- <file>`.

## Dependencies
- Blocked by: None. The file is inert until fabro-workflows task 04 deploys the
  `autofix` node, and harmless before then.
- Why blocked: N/A
- Blocks: None

## Labels
`chore`, `tooling`, `priority:medium`

## Estimate
Small

## Risk
1 - The file is additive, and nothing in writers-app's own CI runs it.

## Validator Stopping Point
Both commands in Test Expectations give their expected output, and `./.fabro/ci.sh`
passes.
