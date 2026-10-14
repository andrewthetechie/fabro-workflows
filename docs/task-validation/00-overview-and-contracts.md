# Task validation: overview and canonical contracts

**Status:** designed 2026-10-14 (issue #26, ADR 0025 proposed). No task started.

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report the
conflict. **Before you edit a `.fabro` file or a `workflow.toml`, invoke the
`/fabro-workflow` skill. Before you edit Rust under `ops/fabro-io/`, invoke `ms-rust`.**
Read `docs/agents/invariants-graph.md`, `invariants-backlog.md` and `invariants-merge.md`
before task 07. Pushing to `main` deploys, so read the rollout order (D13) before any push.

## What this is

`backlog`'s per-task `validate` node runs the full `./.fabro/setup.sh && ./.fabro/ci.sh`
after every coder and rework round. This series replaces it with **Task validation**: the
repository's checks, plus the tests that cover the files the task changed. The gate selects
the tests from the diff. **Final validation**, the full suite `open_pr_prep` already runs
once on the merged branch, is the net. Glossary: `CONTEXT.md`, **Task validation** and
**Final validation**.

## The numbers behind it (issue #26)

- `validate` was 10% of all stage time from 2026-09-25 to 2026-10-11 (72 h): about 24 min a
  run, over a median of 5 visits. It was 11% of all lease time, with the coder box idle.
- It failed deterministically 117 times, and each failure is a rework round.

## Operator decisions (settled; do not change them)

| # | Decision |
|---|---|
| D1 | **Only the per-task `validate` node changes.** Final validation in `open_pr_prep` and its #35 route stay as they are: one `final_fix` (`final_fix_attempts`, never reset), then `rescue` or `draft`. The final-fix budget stays at one. |
| D2 | **The merge phase skips a tree Final validation passed.** `open_pr_prep` records the tree it passed. `_shared/review-merge/` `validate` skips the suite when HEAD's tree equals it and the worktree is clean. `pr-review` and `dependabot` never write the file, so they are unaffected (C8). |
| D3 | **The gate selects the tests, never the agent.** The selection comes from the diff only. No agent output adds to it or removes from it. |
| D4 | **Selection lives in Rust:** `fabro-io task-plan` (C4) prints a JSON plan, and `fabro-io task-check` (C5) runs it. Cargo unit tests cover the rules. The DOT script only calls it and reads the result. |
| D5 | **The repository declares Task validation in `.fabro/test.toml`** (C3): `[task] full` and `skip`, per-target `tests` or `watch`, and `[checks.*]`. The keys are optional. A repository without them gets the full suite per task, as today. |
| D6 | **Fallback to the full suite**, in this precedence: the repository declares no task keys; a changed path matches a built-in or declared `full` glob; a non-test source file was deleted or renamed; the Code index is unavailable; a selected test file is owned by no target and not in `skip`; a selected test is outside its target's `cwd`. The node also falls back when the task is a `final_fix`, or when `task-check` itself errors. |
| D7 | **Untested and unchecked files are recorded and never escalate.** `untested` means a changed source file the index knows, with no importing test. `unchecked` means a changed file that no check watches and no target covers. Both appear in the log, and the unchecked count is published as a context update. |
| D8 | **The conftest rule is built in.** When the reverse-import walk from a changed file reaches a `conftest.py`, every test file under that `conftest.py`'s directory is selected. |
| D9 | **Task validation prepares the Test environment fresh once per visit**, then runs every selected target against that preparation. This keeps ADR 0018's "the gate is never misled by a dirty database". Agent calls use the cache. The preparation seconds are recorded. |
| D10 | **`setup.sh` runs only when a dependency file changed.** Every dependency manifest is a built-in `full` glob, so the scoped path never runs `setup.sh`, and the full path runs it as today. |
| D11 | **Agents get `task_check`** (MCP) and `fabro-test --task` (shell). Both run the same plan against the current diff with the cached environment. The tool gets one Agent guide row and one coder-prompt line, and M-run (`ops/fabro-agent-tools.py`) counts it as a tool test run. |
| D12 | **The scorecard measures it**, and the new row is deployed with history re-summarized *before* the trial starts (C9). |
| D13 | **Rollout order:** the scorecard → `fabro-code` → `fabro-io` 0.5.0 → the images → `min_binary` → the workflow → womens-fantasy-sports → the trial → lawncare-saas, jelly-swipe, writers-app. A repository's `test.toml` must never use the new keys before the images carry 0.5.0: 0.4.x parses strictly, and every `run_tests` and every `ci.sh` (`fabro-io test-env`) would fail. |
| D14 | **Trial on womens-fantasy-sports** (C10): 7 days before against 7 days after its switch, at least 15 runs on each side. It passes when the median per-visit `validate` time is at most 50% of the baseline and red final validations per run rise by no more than 5 percentage points. Rollback: delete the keys from its `test.toml`. On a pass: lawncare-saas, jelly-swipe, then writers-app, each with a 3-day check. |
| D15 | **Records:** ADR 0025 (written; `proposed`), this series, a `docs/README.md` row, a note in `docs/plan-trial/README.md`, and a comment on #26. |

### Defaults drafted while planning (the operator may veto before task 04 lands)

| # | Default |
|---|---|
| P1 | A deleted or renamed non-test source file escalates to the full suite (in D6). Its importers' tests cannot be found once the index has synced the deletion. The changed set uses `--no-renames`, so a rename shows its old path as deleted. |
| P2 | A target whose selected tests number more than 200 runs whole, with no file args. The result is the same, and it avoids an argument list longer than the command line allows. |
| P3 | The gate runs every selected check and target even after one fails, so `rework.md` lists every failure. A preparation failure stops the targets, but the checks have already run. |

## Verified facts (sources)

- **Validation today, per run** (`backlog/workflow.fabro`, `_shared/review-merge/review-merge.fabro`):
  - `validate` (backlog) runs on every visit along `prep_review -> validate`. It runs `setup.sh && ci.sh`, writes `## Validation failed (exit N): ./.fabro/setup.sh && ./.fabro/ci.sh` and the log tail to `/tmp/fabro/feedback/rework.md`, and exits 1.
  - Its edges: `validate -> review [outcome=succeeded]`, plus the unconditional `validate -> rework_router`. It has no `output_schema` and prints the log tail to stdout.
  - `open_pr_prep` runs the full suite with one flake retry and routes on `final_route` (`pr`, `draft`, `fix`, `drop`, `rescue`). It prints `final_route` on green too.
  - The merge phase's `validate` is reached by `fix_gate -> validate` unconditionally (even after `fix_outcome=no_changes_needed`), by `refute_fix_gate -> validate` and by `resolve_base_gate -> validate`. It is `output_schema="routing"`, always exits 0, and publishes `ci_ok` and `fix_attempts`. It is the only local validation in `pr-review` and `dependabot`.
- **The task's diff base.** `prep_review` writes `/tmp/fabro/review/changed_files.txt` from `git diff --name-only $(cat /tmp/fabro/task_base_sha)..HEAD`. `final_fix_prep` sets `current_task.json`'s `final_fix: true`.
- **The fast lane** goes through the same `validate` node (`coder -> task_block_gate -> autofix -> prep_review -> validate`).
- **A routing command node:** it declares `output_schema="routing"` exactly when it prints `context_updates` (`ops/check-graph-invariants.py` R8). Fabro scans stdout *and* stderr for brace-balanced objects, so log text must go through `tr -d '{}'` (see `autofix`). A failed or timed-out node applies no context update and takes its unconditional edge.
- **`rework.md.j2`** lists three feedback shapes and names `## Validation failed (exit N): ./.fabro/setup.sh && ./.fabro/ci.sh` verbatim (line 31).
- **`fabro-code tests PATHS`** (`ops/profile-images/fabro-code`, `tests_()`):
  - It is a recursive SQL walk over `imports` edges to depth 5 under `$TRUST`. Its output is filtered by `TEST_RE`, capped by `cap_out`, and it exits 1 when nothing matches.
  - `TEST_RE` matches `test_*.py`, `*_test.py`, `*.test|spec.[cm][jt]sx?`, `*_test.go`, `__tests__/`, and `tests/*.rs`. It does not match `conftest.py`, or Rust's inline `#[cfg(test)]` modules (writers-app has 46 files with them).
  - The MCP `code_tests` (`ops/fabro-io/src/code.rs`) calls this verb.
- **`test.toml` today** (`ops/fabro-io/src/testenv.rs`): `deny_unknown_fields` on every table. A target has `about`, `cwd`, `run`, `flags`, `value_flags` and `timeout`. `run_tests` is in `runtests.rs`, the tool list is in `serve.rs`, and the subcommands are in `cli.rs`. `fabro-io` is 0.4.1, and `_io/manifest.json` has `min_binary` "0.4.0".
- **The four repositories' `test.toml` are on `main`** (checked 2026-10-14):
  - **womens-fantasy-sports:** `backend` (cwd `backend`, `uv run pytest -n 1 -q`) and `frontend` (no cwd, `bun run --filter frontend test:unit --`, paths relative to `frontend/`). It has a `[prepare]` with `fabro-pg-ensure`, a role encoding fix, and `prestart.sh` (Alembic and seed).
  - **lawncare-saas:** `backend`, `frontend` (cwd `frontend`) and `frontend-admin` (cwd `frontend-admin`), with `fabro-pg-ensure`.
  - **jelly-swipe:** `backend` and `frontend`.
  - **writers-app:** `rust` (cargo filter, 600 s) and `frontend`.
- **womens-fantasy-sports' layout:**
  - Its test files: 168 in `backend/tests`, 143 in `frontend/src`, 32 Playwright specs in `frontend/tests`, and 14 in `frontend/tests-deployed`. `TEST_RE` matches the last two, but vitest excludes them (`frontend/vitest.config.ts` includes only `src/**/*.{test,spec}.{ts,tsx}`).
  - Its `ci.sh` runs `uv run prek run --from-ref origin/main --to-ref HEAD`, the frontend unit tests and build, `backend/scripts/tests-start.sh` (pytest under coverage, at most 8 xdist workers), and `coverage report`.
- **lawncare-saas' layout:** its test files are 101 in `frontend/src`, 10 in `frontend-admin/src`, and the backend tests in `tests/`. Its sources are in `src/backend`, and its tests import `backend.*` directly.
- **The scorecard** (`ops/scheduler/src/fabro_scheduler/`):
  - `summary.py` reduces a run's streamed events. `improve_gate`'s `task_disposition` is read from `stage.completed` `context_updates` (`DISPOSITION_GATE`), and that is the pattern to copy.
  - The collector summarizes every `run_history` row with no `run_summary`, so deleting a window's summary rows re-summarizes it.
  - `ops/fabro-run-prune.sh` prunes only `merge-queue` runs, so `backlog` events are still in fabro.
  - `ops/fabro-scorecard.sh` has no per-repo filter.
- **The Agent guide is 1,791 bytes** against `check`'s 3,000-byte cap.
- **Graph baselines** (AGENTS.md): `Backlog (82 nodes, 210 edges)`. This series adds no node and no edge to any graph.

## Contracts

### C1. The changed set

Computed the same way by the gate and by the agent tool. With base `B`:
`git diff --name-only --no-renames B` (worktree against `B`, so uncommitted edits count),
plus `git ls-files --others --exclude-standard`. The result is sorted and de-duplicated. In
the gate the tree is clean after the stage checkpoint, so this equals `B..HEAD`.

`B` resolves in order: `--base SHA`, then `/tmp/fabro/task_base_sha`, then
`git merge-base HEAD origin/main`.

### C2. `fabro-code tests --json PATH…`

A new form of the existing verb. The plain `tests` output and `code_tests` are unchanged.
The conftest expansion lives here because `TEST_RE` does, so there is one definition of
"a test file".

- It runs the same walk as `tests_()` (same depth, same `$TRUST`) once per seed, carrying the seed.
- It prints exactly one JSON object to stdout, **uncapped**:

```json
{
  "seeds": {
    "src/backend/visits.py": {
      "indexed": true,
      "tests": ["tests/test_visits.py"],
      "conftests": ["tests/conftest.py"]
    }
  },
  "conftest_tests": {
    "tests/conftest.py": ["tests/test_a.py", "tests/test_b.py"]
  }
}
```

- `indexed`: the seed has a row in the index's `files` table.
- `tests`: the files in the seed's closure that match `TEST_RE`. A seed that is itself a test is listed in its own `tests`.
- `conftests`: the files in the closure whose basename is `conftest.py`.
- `conftest_tests`: for each conftest reached, every `git ls-files` path under its directory that matches `TEST_RE`.
- Exit codes: 0 on success, empty answers included. 2 when the index is unavailable or a query fails. 64 on a usage error.

### C3. `.fabro/test.toml`: the Task validation keys (still `version = 1`)

All optional. `deny_unknown_fields` still applies everywhere.

```toml
[task]
full = ["alembic/**"]          # extra globs that force the full suite (added to the built-ins)
skip = ["frontend/tests/**"]   # test files Task validation never runs (e2e, deployed checks)

[targets.backend]              # the existing keys, plus exactly one of tests / watch
cwd = "backend"
run = "uv run pytest -n 1 -q"
tests = ["backend/tests/**"]   # the test files this target runs; selected ones become args

[targets.rust]
run = "cargo test --all-features"
watch = ["src-tauri/**"]       # any changed file here runs the whole target, with no args

[checks.prek]                  # a lint or typecheck step; never prepares the Test environment
about = "pre-commit hooks over the task's changes"   # optional, one line
run = "uv run prek run --from-ref \"$FABRO_TASK_BASE\" --to-ref HEAD"
watch = ["**"]                 # required, non-empty; the check runs when a changed file matches
files = false                  # true: append the matching changed files, relative to cwd
cwd = "."                      # default "."
timeout = 300                  # 1..=600, default 120
```

**Rules:**
- Globs are relative to the checkout root, with `*` (no `/`) and `**` (any depth) semantics. Use the same glob crate the `prepare.inputs` code uses.
- A target may have `tests` or `watch`, never both. A target with neither is still available to `run_tests` but takes no part in Task validation.
- Check names match `^[a-z0-9-]+$`. A check runs with `[env]`, `path`, and `FABRO_TASK_BASE` set to `B`.
- **"Declares task keys"** means: a `[task]` table, or any target with `tests` or `watch`, or any `[checks.*]`. Without any of them `task-plan` returns `mode: full`, `reason: no_config`.
- **Built-in `full` globs**, always applied:
  - `.fabro/**`
  - `**/package.json`, `**/package-lock.json`, `**/bun.lock`, `**/bun.lockb`, `**/yarn.lock`, `**/pnpm-lock.yaml`
  - `**/pyproject.toml`, `**/uv.lock`, `**/requirements*.txt`, `**/setup.cfg`, `**/pytest.ini`, `**/tox.ini`, `**/conftest.py`
  - `**/Cargo.toml`, `**/Cargo.lock`, `**/go.mod`, `**/go.sum`
  - `**/tsconfig*.json`, `**/vite.config.*`, `**/vitest.config.*`, `**/vitest.workspace.*`, `**/jest.config.*`
- **`fabro-io test-env --check` warns** (exit 0, one `warning:` line each). It warns on any tracked test file that no target's `tests` and no `skip` owns, on a test file two targets' `tests` both match, and on a `tests` glob outside its target's `cwd`. The parse errors stay errors.
  - `TEST_RE` lives only in `fabro-code`, so `--check` gets the test files from `fabro-code list-tests`. That is a new verb, `git ls-files | grep -E "$TEST_RE"`, which needs no index.
  - When `fabro-code` is absent (the image build gate runs `--check` on the host), it prints one line saying the ownership warnings were skipped.

### C4. `fabro-io task-plan [--base SHA] [--root DIR]`

It prints one JSON object (the plan) to stdout and always exits 0 unless the arguments are
invalid (64). Every failure becomes `mode: full` with a reason, never an error. The steps:

1. Load `test.toml` (`testenv::TestEnv::load`). If there is none, it is invalid, or it declares no task keys: full, `no_config` (invalid files get `invalid_config`).
2. Compute the changed set (C1). If it is empty: scoped, nothing to run.
3. If any changed path matches a built-in or `[task] full` glob: full, `full_glob:<first path>`.
4. If any changed path no longer exists, is not a test (by C2's classification) and is not in `skip`: full, `deleted:<path>` (P1).
5. Call `fabro-code tests --json` on every changed, existing, non-`skip` path. A non-zero exit or unparseable output means full, `index_unavailable`.
6. Selected tests = the union over the seeds of `tests`, plus `conftest_tests` for every conftest in `conftests`, minus `skip` matches, minus files that do not exist.
7. Map each selected test to the first target, in file order, whose `tests` matches it. If none matches: full, `unowned_test:<path>`. If the path is not under that target's `cwd`: full, `path_outside_cwd:<path>`. Otherwise pass it as an argument relative to `cwd`. A target with more than 200 selected tests runs whole (P2).
8. A `watch` target runs whole when any changed path matches its `watch`.
9. A check runs when any changed path matches its `watch`. With `files = true` its args are the matching paths that still exist, relative to its `cwd`. A check left with no existing files is not run.
10. `untested` = seeds with `indexed: true`, not tests themselves, with empty `tests` and no `conftests`. `unchecked` = changed paths no check's `watch` matches and no target's `tests` or `watch` matches.

```json
{
  "version": 1,
  "mode": "scoped",
  "reason": "",
  "base": "<sha>",
  "changed": ["frontend/src/a.tsx", "backend/app/api/routes/quiz.py"],
  "checks":  [{"name": "prek", "args": []}],
  "targets": [
    {"name": "backend", "args": ["tests/api/routes/test_quiz.py"], "whole": false,
     "why": {"tests/api/routes/test_quiz.py": ["backend/app/api/routes/quiz.py"]}},
    {"name": "frontend", "args": ["src/a.test.tsx"], "whole": false, "why": {}}
  ],
  "untested":  [],
  "unchecked": [],
  "skipped":   []
}
```

In `mode: full`, `checks` and `targets` are empty and `reason` is set. `why` maps each
selected test to the changed paths that selected it. A conftest selection reads
`"<seed> via <conftest>"`.

### C5. `fabro-io task-check [--gate] [--base SHA] [--out FILE]`

It computes the plan (C4), then:

- **`mode: full`.** Runs nothing. Writes the result with `ok: null` and exits 0. The caller decides what to do: the gate runs the full suite, and the agent tool reports the reason.
- **`mode: scoped`.** Runs every check, in file order. Then, if any target is selected, it prepares once: with `--gate` always fresh (`testenv::prepare`), and otherwise only when the stamp's fingerprint differs, as `run_tests` does. Then it runs the targets in file order (P3).
  - Each item uses its own `timeout`. A target's command is built exactly as `run_tests` builds it, flag checks included: the args are paths, so no flag is ever added.
  - It holds `testenv::EnvLock` for the whole call.
- **Without `--gate`**, the total is capped at 600 s, under the MCP `tool_timeout` of 660 s. Items that do not fit are reported `not_run` with the reason "budget".
- **The result**, the plan plus:

```json
{
  "ok": false,
  "prepared_seconds": 41,
  "prepare": {"exit_code": 0},
  "results": [
    {"kind": "check", "name": "prek", "exit_code": 0, "seconds": 12},
    {"kind": "target", "name": "backend", "exit_code": 1, "seconds": 95,
     "tail": "<last 60 lines>"}
  ],
  "log": "/tmp/fabro/task_check.log"
}
```

  - `ok` is true when every item that ran exited 0 and preparation succeeded.
  - The full output of every item goes to `/tmp/fabro/task_check.log`.
  - `--out` writes the result to a file as well as stdout.
- **Exit codes:** 0 when `ok` is true or `mode` is full, 1 when `ok` is false, 2 on an internal error.
- **`fabro-test --task [--base SHA]`** is the shell alias. `fabro-test` passes `--task` through to `fabro-io task-check`, and every other argument to `run-tests`.

### C6. The `task_check` MCP tool

- **Input:** `{}`, with an optional `base`.
- **Description:** "Run the checks and tests the Task validation gate will run for your current changes, using the same selection. Run it before you finish."
- **Listed:** for every manifest stage, like `run_tests` (`serve.rs`).
- **Output:** a short rendering of C5.
  - `mode: full`: "This change needs the full suite (<reason>); the gate will run `setup.sh && ci.sh`. Run the targets that cover your change with run_tests."
  - Scoped: one line per item with its exit code, the failing items' `tail`, and the `untested` list.
- **Agent guide row:** `| what the gate will run for your change, and whether it passes | task_check |`.

### C7. The `validate` node (backlog)

Same id, label "Task validation", `timeout="20m"`, `output_schema="routing"`. The script, in order:

1. Write a placeholder `/tmp/fabro/feedback/rework.md`: `## Validation failed (timeout): task validation did not finish`, then one sentence saying the stage was stopped, most likely on its 20m timeout, and that the log is `/tmp/fabro/validate_output.log`. Today a timeout reaches `rework_router` with the previous round's feedback still in the file. Delete `/tmp/fabro/task_check.json`.
2. If `current_task.json` has `final_fix: true`: mode full, reason `final_fix`.
3. Otherwise run `fabro-io task-check --gate --base "$(cat /tmp/fabro/task_base_sha)" --out /tmp/fabro/task_check.json > /tmp/fabro/validate_output.log 2>&1`. A missing or unparseable result file (an old image has no `task-check`, or the subcommand crashed) means mode full, reason `task_check_error`. Otherwise read `mode`, `reason` and `ok` from it.
4. **Full:** run `./.fabro/setup.sh > … && ./.fabro/ci.sh >> …` exactly as today, writing `/tmp/fabro/validate_output.log`. On failure, the rework file is today's shape, unchanged: `## Validation failed (exit N): ./.fabro/setup.sh && ./.fabro/ci.sh` plus the log tail.
5. **Scoped, red:** the rework file is:

   ```
   ## Validation failed (exit 1): task validation

   Task validation ran the checks and tests that cover this task's changed files. Failed:

   - check `prek` (exit 1)
   - target `backend` (exit 1): tests/api/routes/test_quiz.py

   <each failing item's tail, under a `### <kind> <name>` heading>
   ```

   The whole file is capped at 6,000 bytes of tail, like today.
6. Print to stdout, through `tr -d '{}'`: one summary line (`task validation: mode=… reason=… ok=… selected=N untested=N unchecked=N prepared=Ns`), then the last 4,000 bytes of the log. After that, the one routing object:
   `{"context_updates":{"task_validation_ok":<bool>,"task_validation_mode":"scoped|full","task_validation_reason":"…","task_validation_selected":N,"task_validation_unchecked":N,"task_validation_prepare_s":N}}`.
   Write every key on both branches, `reason` empty and the counts 0 on the full path.
7. Exit 0 on both green and red. A red result routes by context, not by exit code.

Edges (the count is unchanged):
`validate -> review [condition="outcome=succeeded && context.task_validation_ok=true"]`,
and the unconditional `validate -> rework_router` as today. A timeout fails the node, so it
applies no update, and `outcome=succeeded` is false. A stale `task_validation_ok=true`
from an earlier visit can therefore never send it to `review`.

### C8. Final validation records its tree; the merge phase skips it

- **`open_pr_prep`:** `rm -f /tmp/fabro/final_validated_tree` beside its other `rm -f` at the top. Immediately after the retry loop exits with `RC=0`, `git rev-parse 'HEAD^{tree}' > /tmp/fabro/final_validated_tree`.
- **`_shared/review-merge/` `validate`:** before running the suite, read `T=$(cat /tmp/fabro/final_validated_tree 2>/dev/null)`. When `T` is non-empty, `git diff --quiet HEAD` is true, and `git rev-parse 'HEAD^{tree}'` equals `T`:
  - print `validate: skipped, this tree passed final validation in open_pr_prep (<T>)`
  - write the same `needs_human_reason` placeholder as the green branch
  - publish `{"ci_ok":true,"fix_attempts":<A unchanged>}`

  Otherwise it runs as today. Never delete the file here: a later visit after `refute_fix` or `resolve_base` has a different tree, so it runs the suite anyway.

### C9. Scorecard

- **`summary.py`**, per `stage.completed` of the node id exactly `validate` (not `review_merge.validate`): append `{mode, reason_class, ok, ms}` to `run_summary.task_validations`, and add `task_validation_unchecked` to a running total.
  - `reason_class` is the text before the first `:`.
  - `ms` comes from the same timing `run_stage_stats` uses.
  - A visit with no `task_validation_mode`, from a run before the change or a timeout, records `mode: "legacy"` (or `"timeout"` when the stage failed on timeout).
- **Per `stage.completed` of `open_pr_prep`:** append `final_route` to `run_summary.final_routes`. The first element is the run's first final verdict.
- **`scorecard.py`**, a **Task validation** row:
  - visits by mode, with per-visit minutes p50 and p90 for each mode and for all visits together
  - full visits by `reason_class`
  - red visits per mode
  - unchecked files per scoped visit

  And a **Final validation** row: runs that reached `open_pr_prep`, and the first `final_route` split `pr` / `fix` / `drop` / `rescue` / `draft`. **Red final rate** = runs whose first `final_route` is not `pr`, over runs that reached it.
- **`ops/fabro-scorecard.sh --repo OWNER/REPO`** and the API's `repo` parameter filter the window to one repository.
- **A `resummarize` command** (`python -m fabro_scheduler resummarize --from D --to D`, run in the scheduler container) deletes the window's `run_summary` and `run_stage_stats` rows, and only those, so the collector rebuilds them with the new fields.

### C10. The trial measurement

- **Baseline:** `ops/fabro-scorecard.sh --repo andrewthetechie/womens-fantasy-sports --from <switch−7d> --to <switch−1d>`, after C9 is deployed and that window re-summarized.
- **After:** the same for `<switch>` to `<switch+6d>`. The switch date is the merge time of the womens-fantasy-sports `test.toml` PR (task 09).
- Each side needs at least 15 runs; otherwise extend the after-window a day at a time.
- **Pass** needs both:
  - (a) the median per-visit `validate` minutes over all visits after the switch is at most 50% of the baseline median
  - (b) the red final rate after the switch is at most the baseline rate + 5 points
- The result goes in `docs/task-validation/result-<date>.txt`.

## Tasks

| # | File | Where it runs |
|---|---|---|
| 01 | `01-scorecard.md`: C9, deploy, re-summarize history | this repo + host |
| 02 | `02-fabro-code-json.md`: C2 and `list-tests` | this repo |
| 03 | `03-test-toml-keys.md`: C3 parse and `--check` warnings | this repo (Rust) |
| 04 | `04-task-plan.md`: C1, C4 | this repo (Rust) |
| 05 | `05-task-check.md`: C5, C6, `fabro-test --task`, 0.5.0 | this repo (Rust) |
| 06 | `06-images.md`: `make deploy-images`, `min_binary` | this repo + host |
| 07 | `07-graph.md`: C7, C8, and `ops/test-task-gates.sh` | this repo |
| 08 | `08-agents-and-docs.md`: prompts, Agent guide, M-run, invariants, records | this repo |
| 09 | `09-womens-fantasy-sports.md`: its `test.toml` keys | womens-fantasy-sports |
| 10 | `10-trial.md`: C10 | this repo + host |
| 11 | `11-other-repos.md`: lawncare-saas, jelly-swipe, writers-app | three target repos |

Tasks 01 and 02 are independent of everything else. Task 03 needs nothing, 04 needs 02 and
03, and 05 needs 04. Task 06 needs 02 and 05. Tasks 07 and 08 need 06 and are pushed
together. Task 09 needs 07, 08 and 01. Task 10 needs 09 plus 7 days, and task 11 needs a
pass in 10.
