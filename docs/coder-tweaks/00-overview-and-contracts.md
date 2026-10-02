# Coder tool use: overview and canonical contracts

**Status:** tasks 01-07 implemented 2026-10-01 and deployed 2026-10-02 (fabro-io 0.3.0); a review fix (fabro-io 0.3.1: `restore_file`'s base, the guard's shell reserved words, the `git-guard:` reason prefix, the guide's git exception) needs `make deploy-images`. Task 08's measurement is pending. Each task file records its own deviations. The earlier change
it builds on, `24eb95c` (the `excerpts` node, the coder prompt rules, and the fabro-io
0.2.0 `code_*` tools), is live. Its follow-up check is in `handoff.md`.

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report
the conflict. **Before you edit a `.fabro` file or a `workflow.toml`, invoke the
`/fabro-workflow` skill. Before you edit Rust under `ops/fabro-io/`, invoke `ms-rust`.**

## What this is

The local model on the coder boxes (`coders-a`/`coders-b`, DeepSeek on llama.cpp) runs
`coder`, `improve` and `rework_t1`. This series has two goals:

1. **Steer it to the code-index tools** (`code_*`, `fabro-code`) and away from text search
   and whole-file reads, **without denying any search or read tool**.
2. **Stop agents from running mutating git**, which AGENTS.md forbids for every stage but
   two. Mutating git here means commands such as `git stash`, `git checkout -- <file>` or
   `git fetch`.

## Baseline (20 `backlog` runs, 01M3RXPP… to 01M3X2GD…, 2026-09-30 to 2026-10-02)

Only the stages a `coders-*` model served (`coder`, `improve`, `rework_t1`), about 3,800
tool calls in all:

| Measure | Value |
|---|---|
| `fabro-code` calls (shell) | **6** (4 in the first scratch count; see `baseline-2026-10-02.txt`; no `code_*` tool calls: they are not in the images yet) |
| text search: native `grep` / shell `grep` / `rg` | 599 / 706 / 87 |
| `read_file` | 1,435 calls, **9.0 MB** returned, 616 (43%) of them whole files |
| shell views: `head` / `tail` / `cat` / `sed` / `python` | 377 / 357 / 79 / 71 / 23 |
| `inputs` output | 6.2 MB (the repomap and the issue, in every stage) |
| `edit_file` | 569 calls, **1 error**. The old text is 262K characters, 38% of edit output. |

Mutating git, counted by contract C2's rule over all stages and models by
`ops/fabro-agent-tools.py`: **11 shell calls in 9 stage visits**. 5 were in `coder`, 4 in
`improve`, and 1 each in `rework_t1` and `spec`. By command segment: `stash` 12, `checkout` 3,
`fetch` 1. (The first scratch count said 15 calls in 13 visits; it counted segments, not calls.)

What the agents were trying to do with git:

| Intent | Example | Danger |
|---|---|---|
| "Is this failure pre-existing?" | `git stash && uv run mypy x.py; git stash pop` | A failed `pop` or a partial stash strands the work, and the checkpoint commits whatever is left in the tree. |
| "Revert this file" | `git checkout -- a.rs b.ts` | Restores HEAD, not the task base, and silently discards the agent's own edits. The coder prompt invites it ("Any file you don't recognize… → revert it"). |
| "Prove the test catches the bug" | break `App.tsx`, run the test, then `git checkout -- App.tsx` | Discards every earlier edit to that file. |
| `improve` editing at all | edited 4 tracked files, then `git checkout --`; `git stash push --include-untracked` and `pop` over the coder's work | `improve` is read-only. Any edit it leaves behind is committed by its checkpoint. |

## Operator decisions (settled; do not change them)

| # | Decision |
|---|---|
| D1 | **Steer, never deny, for search and reading.** No hook, wrapper or prompt rule blocks `grep`, `rg`, `glob`, `read_file`, `sed`, `cat` or `head`. A model denied `grep` turns to `sed`, `python` or shell tricks. The better tools have to win on their own merits. |
| D2 | **Mutating git is blocked** in every agent stage except backlog `resolve_merge` and pr-review `rebase_agent_t1`/`rebase_agent_t2`. The block reason names the safe alternative (C2). The guard catches mistakes and is not a security boundary: `sh -c`, `python` or `bash` around the command bypass it, as they do `io-guard` (ADR 0016 D8). |
| D3 | **hashline is deferred.** `edit_file` fails 1 time in 569, so the reliability case does not apply. The token case (38% of edit output, about 3-4 minutes per run) is smaller than the read and search costs this series targets, and adoption has the same DeepSeek problem as `fabro-code`. Revisit after task 08. If pursued, put the anchors inside the `code_*` family (`code_show` prints them and a `code_edit` applies them), not in a second tool family. |
| D4 | **No fork of pebble or fabro.** The levers are the instruction files pebble loads, our MCP tools, the stage inputs, hooks and the profile images. |
| D5 | **Out of scope:** a PATH wrapper around `grep`/`rg` that adds index hints, and changing the pebble agent profile through `metadata.agent.profile`. Both are possible follow-ups if task 08 shows the steering failed. |

## Verified facts (sources)

- **Pebble's system prompt pushes `rg`.** Box models use pebble's `openai` profile, because
  an operator provider with no `metadata.agent.profile` gets the profile its codec implies
  (`context/fabro/lib/components/fabro-llm/src/catalog.rs`, `adapter_agent_profile`).
  That profile's prompt says *"When searching for text or files, prefer `rg` (ripgrep)"*,
  and it documents only the native tools (`pebble-coding-agent/src/profiles/prompts/openai.md.j2`).
  MCP tools appear only as tool-list entries (`mcp__io__code_def`). Every agent stage
  on this host, including the hosted GLM ones, logs `"profile":"openai"` in
  `agent.memory.loaded`.
- **Pebble puts project instruction files into the system prompt.** For the `openai`
  profile it reads `AGENTS.md`, then `.codex/instructions.md`, from the git root down to
  the cwd (`pebble-coding-agent/src/types.rs`, `memory_filenames`). The budget is
  **32,768 bytes across all files**, and the file that crosses it is cut
  (`memory.rs`). Today every session loads only the repo's `AGENTS.md` (lawncare 11,789 B,
  womens-fantasy-sports 10,821 B, jelly-swipe 8,291 B, writers-app 1,786 B) and finds no
  `.codex/instructions.md`.
- **Hooks can only proceed, skip or block.** `HookDecision` is
  `Proceed | Skip | Block | Override{edge_to}` (`fabro-hooks/src/types.rs`). A
  `pre_tool_use` Block returns the reason to the agent as a failed tool call
  (`bridge.rs`). There is no way to rewrite a tool's input or add to its output.
- **Any non-zero hook exit is a Block.** Exit 0 proceeds, exit 2 blocks, and **any other
  exit also blocks** (`executor.rs`, `parse_decision`). A sandbox hook that cannot run
  or times out reports exit `-1`, which also blocks. A guard that crashes, or is missing
  from an old image, therefore fails **every** call it matches. Contract C2's wrapper
  exists for this.
- **The native `grep` tool runs `rg -H --line-number --no-heading --null`** (or
  `grep -rnIH --null` when `rg` is missing) inside the sandbox, and parses
  null-separated records (`sandbox-driver/src/derived/search.rs`). That is why D5 keeps
  wrappers out.
- **`fabro-io serve` runs with cwd `/workspace`.** The checkout is `/workspace/<repo>`
  (`ops/fabro-io/src/code.rs`, `find_checkout`).
- **Every stage writes a checkpoint commit** (`git add -A && git commit --allow-empty`)
  before the next node runs. So `improve`'s edits are already in `HEAD` when
  `improve_gate` starts, and `HEAD^` is the previous stage's checkpoint.

## Contracts

### C1. The agent guide: tool guidance in the system prompt

- **Source:** `.fabro/workflows/_io/agent-guide.md`, at most **3,000 bytes**, plain
  Markdown with no `'''`.
- **Delivery:** `ops/fabro-io-manifest.py generate` writes it into every workflow's
  `workflow.toml` `[run.environment.env]` as `FABRO_AGENT_GUIDE = '''…'''`, between
  generated markers next to the manifest block. `check` fails on drift, a size over
  3,000, or a `'''`. Changing the guide therefore needs no image rebuild.
- **Install:** each workflow's entry node (backlog `prep`, pr-review `claim`, arch-review
  `prep`, issue-triage `acquire`, the same four that run the code-index lines) writes
  `$FABRO_AGENT_GUIDE` to `<checkout>/.codex/instructions.md`, and adds
  `.codex/instructions.md` to `.git/info/exclude` once, the same way it adds
  `.codegraph/`. It skips both when the variable is empty, and when the repo **tracks**
  `.codex/instructions.md` (`git ls-files --error-unmatch`). In either case it prints one
  line to stderr and never fails the node.
- **Content rule:** the guide maps intents to tools. A name → `code_def`. A function's or
  class's source → `code_show` (or `path:line`). Callers → `code_callers`. Text that is
  not a code name (strings, config keys, error messages) → `code_search` or `grep`. A
  failure that may predate your change → `baseline_check`. Undo your change to a file →
  `restore_file`. It names the `fabro-code` shell fallback for when the tools are absent,
  says that `grep` is fine for text, and contains no stage-specific rule.

### C2. Git policy and the git guard

- **Allowed everywhere** (read-only): `status`, `diff`, `log`, `show`, `blame`, `grep`,
  `ls-files`, `ls-tree`, `rev-parse`, `merge-base`, `cat-file`, `range-diff`,
  `describe`, `shortlog`, `rev-list`, `diff-tree`, `name-rev`, `for-each-ref`,
  `check-ignore`, `version`, `help`; `stash list|show`; `branch` with no arguments or
  only listing flags; `worktree list`; `config --get*`. Global options (`-C <dir>`,
  `-c k=v`, `--no-pager`) are skipped before the subcommand is read. A `git` after a shell
  reserved word (`if`, `then`, `else`, `elif`, `do`, `while`, `until`, `!`) is at command
  position too.
- **Everything else is mutating** and is blocked outside the exempt stages, `fetch`
  included.
- **Exempt stages** carry `"git": "write"` in their `_io/manifest.json` entry: backlog
  `resolve_merge`, pr-review `rebase_agent_t1` and `rebase_agent_t2`. Any other value,
  or no value, means read-only.
- **Guard:** `fabro-io git-guard` reads `FABRO_HOOK_CONTEXT`. It acts only when
  `tool_name == "shell"`, and it scans every `git` invocation in
  `tool_input.command`. It reads the stage from `.io/stage.json`. On a violation it
  prints `{"decision":"block","reason":"<C2 reason>"}` and exits 2. Otherwise, and on any
  error of its own, it exits 0.
- **Hook**, in every workflow whose agents run `shell` (backlog, pr-review, arch-review,
  issue-triage): `event = "pre_tool_use"`, `matcher = "^shell$"`, `blocking = true`,
  `sandbox = true`, `timeout = "5s"`, and the script
  `fabro-io git-guard; [ $? -eq 2 ] && exit 2; exit 0`. The wrapper is required: without
  it, an image older than the subcommand would block every shell call (see the exit-code
  fact above).
- **Reason text:** it starts with `git-guard: ` (fabro passes it to the agent unchanged, and
  `ops/fabro-agent-tools.py` tells a blocked call from an executed one by it), and names the
  blocked subcommand and the alternative.
  - For `stash` and `checkout -- <path>`/`restore`: "use `restore_file` to undo your
    change to a file; to see whether a failure predates your change, use
    `baseline_check`".
  - For `commit`/`add`/`push`: "the checkpoint commits your working tree after this
    stage; leave changes uncommitted".
  - For `fetch`/`pull`/`merge`/`rebase`: "the graph's command nodes own branch and
    remote state".

### C3. `code_search`

- An MCP tool in fabro-io, backed by a new `fabro-code search <pattern> [path]` verb.
  Arguments: `pattern` (required, a regex), `path` (optional, repo-relative).
- **Output:** matches grouped by file and then by the **enclosing symbol** (the narrowest
  index node holding the line, as `show path:line` resolves it), each group headed
  `path:start-end kind name`. When `pattern` is an exact symbol name that the index can
  resolve, the definition comes first. At most 200 lines, with the same `(N lines cut)`
  footer as the other verbs.
- **Never worse than `grep`:** with no index, or when the pattern matches no symbol,
  the result is plain `path:line:text` matches. It searches with `rg` when present, and
  `grep -rnI` otherwise.

### C4. `restore_file` and `baseline_check`

- **`restore_file(path)`** restores one repo-relative path to its content where the
  agent's work started, or deletes it if it did not exist there: `/tmp/fabro/task_base_sha`
  in backlog's task loop, and `HEAD` (the stage's start) once `/tmp/fabro/run_base_sha`
  exists or when neither file does. Never a merge base with `base_ref`, which in pr-review
  is `main` (amended 2026-10-02 after the review; see `04-git-safe-tools.md`). It refuses a path
  outside the checkout and a path under `.github/workflows/`, and it prints what changed.
  It runs git inside fabro-io, which is allowed: the guard watches the agent's
  `shell`, not our tools.
- **`baseline_check(command)`** runs `command` in a **separate worktree** at
  `run_base_sha`, else `task_base_sha`, else the merge base with `base_ref`
  (`git worktree add --detach /tmp/fabro/base-tree <base>`, created once per
  task). The cwd is the same relative directory as the agent's. It returns the exit code
  and the last 200 lines of output. It never touches the agent's checkout. Dependency
  sharing per ecosystem (`UV_PROJECT_ENVIRONMENT`, a `node_modules` symlink,
  `CARGO_TARGET_DIR`) is settled by task 04's spike. A repo the spike could not make
  work gets a tool that answers "baseline_check is not available in this repository;
  note the failure and move on".
- `next_task` removes the worktree (`git worktree remove --force`, then
  `git worktree prune`) when it starts a task.

### C5. The `improve` backstop

`improve_gate` first runs `git diff --quiet HEAD^ HEAD`. When `improve`'s checkpoint
changed the tree, it runs `git revert --no-edit --no-commit HEAD`. The gate's own
checkpoint then commits the revert. It writes `improve_touched_tree` (the file count) to
`/tmp/fabro/improve_touched_tree` and prints one line naming the files. The disposition
logic is unchanged. A stray edit is undone and recorded, and never fails the task.

### C6. Call sites in `task-code.md`

`excerpts` appends a section `## Where these are used` after the code blocks. For each
symbol the dossier names in backticks under `## Files and symbols`, it lists
`fabro-code callers NAME`. That is at most 15 call sites per symbol, each
`path:line  enclosing-symbol` followed by the call line itself. The whole file stays
under the existing 40,000-byte cap, with code blocks first.

### C7. Metrics (computed by `ops/fabro-agent-tools.py`, task 01)

| Id | Metric | Baseline | Target after task 08 |
|---|---|---|---|
| M1 | code-index share of searches: `code_*` + `fabro-code` ÷ (those + native grep + shell grep/rg) | 0.4% | ≥ 25% |
| M2 | `read_file` bytes per local-model stage visit | 49,077 (184 visits) | −40% |
| M3 | whole-file share of `read_file` | 43% | < 20% |
| M4 | mutating git **executed** outside exempt stages | 11 calls / 20 runs | 0 (blocked attempts are reported separately) |
| M5 | `improve` visits that edited a tracked file (attempt-based proxy) | 4 of 87 | 0 reach `coder` |
| M6 | agent sessions whose `agent.memory.loaded` lists `.codex/instructions.md` | 0% | 100% of sessions in runs after task 02 |
| M7 | coder start → first edit (median), see `handoff.md` | 4.7 min over all 84 coder visits (17 min was the two 2026-10-01 runs) | ≤ 6 min |
| M8 | review first-pass rate and rework escalations per task | 93.0% (80 of 86); 0.15 reworks per task | no worse |

## Tasks

| # | File | What | Needs an image rebuild |
|---|---|---|---|
| 01 | `01-measure.md` | Commit the measuring script and record the baseline | no |
| 02 | `02-agent-guide.md` | C1: the guide in the system prompt | no, but it names the tools from 03 and 04 |
| 03 | `03-code-tools.md` | Sharper `code_*` descriptions and C3 `code_search` | yes |
| 04 | `04-git-safe-tools.md` | C4 `restore_file` and `baseline_check` (spike first) | yes |
| 05 | `05-git-guard.md` | C2 guard and hooks, plus the prompt lines that invite git | yes (the guard) |
| 06 | `06-improve-backstop.md` | C5 | no |
| 07 | `07-excerpts-call-sites.md` | C6 | no |
| 08 | `08-deploy-verify.md` | Image rebuild, rollout order, measurement against C7 | — |

Order: 01 first. 03 and 04 before 05, so that the guard's reasons point at tools that
exist. 02 can land any time after 03 and 04 are merged, because the guide describes those
tools; until the images are rebuilt it falls back to `fabro-code`. 06 and 07 are
independent. 08 last.

## Rules every task follows

1. Validate per AGENTS.md before every push: `fabro validate` in the container (state
   the new node and edge counts), the routing checker, `python3.11
   ops/fabro-io-manifest.py check`, `cargo test` in `ops/fabro-io/`, and
   `./ops/test-task-gates.sh`, on the Mac and inside at least `fabro-python-node` and
   `fabro-ts`.
2. Every new or changed command-node script is POSIX `sh`, with `\"` as its only
   backslash and no `#` comments inside `script=`. It is covered in
   `ops/test-task-gates.sh` with and without `fabro-code` on `PATH`.
3. No hook may block when its own program is missing or broken (C2 wrapper).
4. A pushed graph is live on the next fire. A pushed `ops/` change is live only after
   `make deploy` (or the one step it needs). Say which applies in the commit message.
5. Append results to this folder rather than editing the baseline. `handoff.md` is the
   earlier change's check; keep it.
