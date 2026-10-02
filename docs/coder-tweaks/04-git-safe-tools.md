# 04. Safe tools for what agents use git for: `restore_file` and `baseline_check` (C4)

## Outcome
The two jobs agents did with `git stash` and `git checkout --` each have a tool that does
the job without touching git state:
- `restore_file(path)` undoes the agent's change to one file;
- `baseline_check(command)` answers "does this fail on the base commit too?" in a separate
  worktree.

The guard in task 05 points at these tools. Without them, a block would only push the
model toward a workaround.

## Why
See the overview's intent table. Every observed `stash` was a "pre-existing?" check
(mypy, ruff, pytest) or a mutation test. Every `checkout --` was a revert. The intent is
reasonable each time; only the mechanism is dangerous.

## Change
1. **Spike first (record the result in this file before you build).** In each of the four
   profile images, with the matching target repo cloned and set up as `prep` does it:
   ```sh
   git worktree add --detach /tmp/fabro/base-tree "$(git rev-parse HEAD~1)"
   ```
   Then find the cheapest way for the repo's narrow checks to run there **without** a new
   dependency install:
   - Python with uv: `UV_PROJECT_ENVIRONMENT=<checkout>/.venv uv run --frozen ...` from
     the worktree;
   - Node and bun: symlink each `node_modules` directory found under the checkout into the
     same place in the worktree;
   - Rust: `CARGO_TARGET_DIR=<checkout>/target`.

   Record, per repo, which commands worked (`ruff`, `mypy`, `pytest -k`, `vitest`,
   `cargo test -p`), and whether any of them wrote into the checkout. A repo where nothing
   works gets C4's "not available" answer.
2. `ops/fabro-io/src/`: a new module for both tools, listed for every known stage like
   `code_*`.
   - **`restore_file`:** `path` is required.
     - Resolve it inside the checkout; refuse `..`, absolute paths outside it, and
       `.github/workflows/`.
     - Run `git cat-file -e <base>:<path>`. If the path exists at base, run
       `git show <base>:<path>` into the file; if not, delete it.
     - The base sha comes from `/tmp/fabro/task_base_sha`. If that file is missing (pr-review,
       arch-review), use `git merge-base HEAD origin/<base_ref>` from `/tmp/fabro/base_ref`.
       If neither is available, return an error saying so.
     - Return a one-line summary.
   - **`baseline_check`:** `command` (string) and optional `timeout_s` (default 120,
     maximum 600; above the MCP `tool_timeout`, see Risk).
     - Create the worktree on first use and reuse it within the task.
     - Apply the spike's per-repo environment, detected from files in the checkout:
       `uv.lock`, `bun.lockb`/`package-lock.json`, `Cargo.toml`.
     - Run `bash -c "$command"` with cwd = the worktree's matching subdirectory (the
       agent passes `cd x && …` itself, so default to the worktree root).
     - Return `exit=<n>` plus the last 200 lines.
3. `backlog` `next_task`: before selecting the task, run
   `git worktree remove --force /tmp/fabro/base-tree 2>/dev/null; git worktree prune` (no
   stdout; this node routes).
4. The MCP `tool_timeout` is 30s (`[run.agent.mcps.io]`). `baseline_check` needs longer.
   Raise `tool_timeout` for the `io` server to `660s` in all four `workflow.toml`s, and
   say in the comment why. `inputs`/`submit` are fast, so the raise only matters when a
   tool hangs. A hung `inputs` call would then cost up to 11 minutes instead of 30
   seconds, which is the trade-off to record.

## Acceptance
- `cargo test`: `restore_file` (an existing file restored, a new file deleted, refusal of
  `../x` and `.github/workflows/ci.yml`) and `baseline_check` (runs in the worktree, never
  changes the checkout, the timeout kills the command), against a real-git temp repo.
- Spike results are in this file per repo.
- `ops/test-task-gates.sh`: `next_task` removes a stale worktree and still prints exactly
  one routing object.

## Depends on
01. It needs `make deploy-images`.

## Risk
- **Shared `.venv`/`node_modules`:** if a check writes into them, the agent's environment
  changes. That is acceptable for read-only checks, but the spike must check it.
- **The 660s tool timeout:** the alternative is a background job that the agent polls,
  which DeepSeek will not use reliably. Take the timeout.

## Spike results (2026-10-01)

Run on the host, in `fabro-python-node:local`, against a copy of `lawncare-saas` (the
sandbox clone is shallow, so the base was made by committing a marker edit and adding the
worktree at `HEAD~1`). The other three repositories were **not** spiked: the build tree
holds their clones but each needs its own dependency install, and nothing below was
measured for them. They get the same generic detection; the doc says so rather than
claiming a result.

**Python (uv), lawncare-saas.**
- **A plain `uv run` in the worktree breaks the agent's environment.** With
  `UV_PROJECT_ENVIRONMENT=<checkout>/.venv`, `uv run --frozen` syncs the shared venv and
  re-points its editable install at the worktree. The agent's own checkout then imports
  the **base** code (`backend.__file__` moved to `/tmp/base-tree/src/...`, and the agent's
  marker attribute vanished). The plan in this file would have shipped that.
- **`UV_NO_SYNC=1` plus `PYTHONPATH=<worktree>/src` is correct.** The venv stays pointed at
  the checkout (verified after the run), and imports resolve to the worktree and the base
  content. `ruff check` ran in 0.05 s. `pytest` ran, but the lawncare suite needs a
  PostgreSQL the sandbox only has inside a stage, so 13 DB tests errored here; that is the
  environment, not the method. `mypy` is not in lawncare's dev dependencies, so `uv run
  mypy` failed to spawn in the base and in the checkout alike.
- Nothing wrote into the checkout (`git status` clean).

**Node (npm workspaces), lawncare-saas.**
- Symlinking the checkout's `node_modules` (root, found by walking the tree) into the
  worktree works: `vitest run` in `frontend/` passed 1,214 tests in 23 s. It wrote
  `frontend/junit/frontend.xml` into the **worktree**, not the checkout.
- Caveat: the workspace packages inside the shared `node_modules` are relative symlinks
  (`node_modules/frontend -> ../frontend`). They resolve against the **checkout**, so a
  test that imports a sibling workspace package sees the agent's version of it. Same-package
  imports resolve in the worktree.

**Rust.** Not spiked. A cold `cargo` build of writers-app's Tauri workspace in a worktree
was not measured, so `baseline_check` sets `CARGO_TARGET_DIR=/tmp/fabro/base-target` (the
agent's `target/` is never written) and leaves the cost to the caller's `timeout_s`. A
repository with only `Cargo.toml` and no `node_modules` or `.venv` gets the "not available"
answer.

The tool therefore does what the spike supports: link every `node_modules`; for the shallowest
`.venv` that has a `uv.lock` or `pyproject.toml` beside it, set `UV_PROJECT_ENVIRONMENT`,
`UV_NO_SYNC`, `UV_FROZEN`, `VIRTUAL_ENV`, `PATH` (the venv's `bin` first) and `PYTHONPATH`.
The tool description tells the model to run its normal command (`uv run pytest ...`,
`npx vitest run ...`).

## Amendment after review (2026-10-02, fabro-io 0.3.1)

The first build used one base for both tools: `task_base_sha`, else the merge base of `HEAD`
and `origin/<base_ref>`. That was wrong for `restore_file` outside backlog's task loop:

- In pr-review there is no `task_base_sha`, so the merge base was `main`, and restoring a
  file erased the PR author's change to it.
- In backlog's merge phase `task_base_sha` is the last task's base, so restoring a file also
  dropped that task's work on it.

Both are reachable: the guide and every `git-guard` reason tell the agent to use
`restore_file`. The bases are now separate (`gitsafe::restore_base`, `gitsafe::baseline_base`):

| Tool | backlog task loop | after `run_base_sha` exists, or no base file |
|---|---|---|
| `restore_file` | `task_base_sha` (so rework can undo the coder's out-of-scope edit) | `HEAD`, the stage's start (agents cannot commit, and every stage checkpoints) |
| `baseline_check` | `task_base_sha` | `run_base_sha` (backlog: the branch point; pr-review: the PR head it claimed), else the merge base with `base_ref` |
