# Merge rate and run size — overview and canonical contracts

**Read this first.** Every task in this folder assumes the decisions, contracts and rules
recorded here. Each task file repeats what it needs, but where a task and this file
disagree, this file wins. Then stop and report the conflict. Do not guess.

Source decision: `docs/adr/0011-merge-rate-and-run-size.md` (ADR 0011). This series is
its implementation plan.

## What this is

Scheduler-dispatched `backlog` runs open pull requests, and a shared merge phase is meant
to squash-merge them. From 2026-09-19 to 2026-09-24 it merged **4 of 34** PRs. The
operator merged the rest by hand. The causes, measured:

- Every stage pushes an empty checkpoint commit. That floods CI and moves the PR head
  after CI has already passed.
- `ci_fix` gives up on flaky tests.
- Two repositories' local `ci.sh` skips the backend tests that GitHub CI runs.
- `ci_fix_t1` times out on the local coder box.
- Runs grow to 9–22 tasks and produce PRs of 30–72 files that do not merge.

This series fixes those causes and adds two changes that make runs shorter.

## Glossary (from `CONTEXT.md`, the words used below)

- **Run**: one execution of a workflow graph on the fabro server.
- **Task**: one unit of work `decompose` produces **inside** a run. A run implements its
  tasks one after another, each through `improve → coder → review`.
- **Queue item**: an open issue labelled `agent` that is neither `agent-in-progress` nor
  `agent-stuck`. The scheduler turns one queue item into one `backlog` run.
- **Scheduler**: `ops/scheduler/`, a Python service that reads issues from GitHub,
  orders them, and starts `backlog` runs. Not fabro's own scheduler.
- **Override**: the operator's "dispatch this next" click in the scheduler web UI,
  stored in the scheduler's SQLite `overrides` table.
- **Task budget**: the most tasks one run implements. It is **8**.
- **Remainder issue**: the issue a run files for the tasks it did not start because its
  task budget was spent.
- **Priority label**: the GitHub label `priority`. The scheduler ranks a queue item that
  carries it right after an Override. It is **not** "repo priority", the integer in
  `ops/scheduler/repos.toml`.

## Operator decisions (settled; do not change them)

| # | Decision |
|---|---|
| 1 | `backlog/workflow.toml` sets `[run.run_branch] push = false` (and keeps `enabled = true`). |
| 2 | `open_pr` widens the git fetch refspec for the run branch, right after its first push. |
| 3 | The shared `merge` node gets `timeout="50m"`. |
| 4 | `watch_checks` reruns failed GitHub Actions jobs **once** per merge phase, and only when ≥ 30 minutes of `merge_deadline` remain **and** the node has run < 20 minutes. |
| 5 | `ci_fix_t1` uses a new stylesheet class `ci-fix`, which resolves to `glm-5.3-flash` in **both** `backlog` and `pr-review`. |
| 6 | lawncare-saas's `.fabro/ci.sh` runs its backend pytest suite and its API-client staleness check. |
| 7 | womens-fantasy-sports gets an issue, not a code change, for its backend suite. |
| 8 | An optional per-repository file `.fabro/fix.sh` holds formatters and machine-applicable lint fixes only. A new `backlog` node, `autofix`, runs it after every coder or rework stage. `autofix` **always exits 0**. |
| 9 | writers-app gets the first `.fabro/fix.sh`. |
| 10 | Task budget = **8**, counted as tasks routed to `coder`. `next_task` enforces it. |
| 11 | Tasks over budget go to `/tmp/fabro/remainder.json`. A new node `file_remainder`, placed between `open_pr` and `pr_handoff`, files them as **one** remainder issue labelled `agent-remainder` + `ai-generated` (**not** `agent`), and posts one PR comment naming it. |
| 12 | The scheduler **promotes** remainder issues. When the parent PR has merged, it adds `agent` + `priority` and removes `agent-remainder`. When the parent PR closed unmerged, it adds `agent-stuck` and removes `agent-remainder`. While the PR is open, it does nothing. |
| 13 | The scheduler ranks a queue item with the `priority` label after Overrides and before everything else, oldest first. |
| 14 | `render.jq` and the merge-phase report comments are **not** changed. |
| 15 | Not in scope, declined by the operator: moving `improve` to a hosted model, releasing the coder lease early, a hosted rebase, a hosted coder pool, and loosening `ci_fix`'s file boundary. |

## Canonical contracts

### C1. Remainder issue marker (written by task 10, read by task 09)

The **first line** of a remainder issue's body is exactly:

```
<!-- fabro:remainder parent=<PARENT_ISSUE_NUMBER> pr=<PR_NUMBER> -->
```

- Both values are decimal integers with no `#` and no spaces around `=`.
- Example: `<!-- fabro:remainder parent=1239 pr=1250 -->`
- The scheduler matches it with this Python regex (compile once, search anywhere in the
  body): `<!-- fabro:remainder parent=(\d+) pr=(\d+) -->`
- A body with no match is logged and left alone. It is never promoted and never
  labelled stuck.

### C2. Labels

| Label | Colour | Who adds it | Meaning |
|---|---|---|---|
| `agent-remainder` | `C5DEF5` | `file_remainder` | A remainder issue held until its parent PR merges. |
| `ai-generated` | `EDEDED` | `file_remainder` | The issue was written by an agent. Already exists in 3 of 4 repositories. |
| `priority` | `B60205` | the scheduler, or the operator | Rank this queue item next. |
| `agent` | existing | the scheduler (on promotion) | Makes the issue a queue item. |
| `agent-stuck` | existing | the scheduler (orphaned remainder) | A human must look. Excluded from the queue. |

`priority` and `agent-remainder` exist in **no** repository today, and writers-app lacks
`ai-generated`. `file_remainder` creates all three with
`gh label create <name> --color <hex> 2>/dev/null || true` before it uses them. That is
the same idempotent pattern `claim` uses today:

```sh
gh label create agent-in-progress --color FBCA04 2>/dev/null || true
gh label create agent-stuck --color D93F0B 2>/dev/null || true
```

lawncare-saas and womens-fantasy-sports also have an unrelated `do-next` label. Do not
read it and do not reuse it.

### C3. Run-local files under `/tmp/fabro/` (the backlog sandbox)

| Path | Written by | Read by | Shape |
|---|---|---|---|
| `/tmp/fabro/tasks.json` | `decompose_gate`, `improve_gate` (split), `extra_gate` | `next_task` | JSON array of task objects `{id, title, body, files, covers, source}` |
| `/tmp/fabro/task_index` | `next_task`, `improve_gate` | `next_task` | integer: index of the next task to select |
| `/tmp/fabro/tasks_coded` | `prep` (reset to 0), `improve_gate` (+1 on `ready`) | `next_task` | integer |
| `/tmp/fabro/remainder.json` | `prep` (deleted), `next_task` (append) | `extra_prep`, `file_remainder` | JSON array of task objects, same shape as `tasks.json`, no duplicate `id` |
| `/tmp/fabro/remainder_issue` | `file_remainder` | `file_remainder` (idempotency) | the remainder issue number, digits only |
| `/tmp/fabro/ci_rerun_done` | `watch_checks` | `watch_checks` | empty marker file |
| `/tmp/fabro/issue.json` | `claim` | many | `gh issue view` JSON; `.number` and `.title` are used here |
| `/tmp/fabro/pr_number` | `open_pr` | many | digits only |

### C4. `.fabro/fix.sh` (per target repository)

- It is optional. If it is absent, `autofix` does nothing and succeeds.
- It is run from the repository root as `./.fabro/fix.sh`. If it is not executable,
  `autofix` runs `bash ./.fabro/fix.sh`. Every target repository's `ci.sh` already
  starts with `#!/usr/bin/env bash`, so bash is present in every sandbox image.
- It may contain only formatters and machine-applicable lint fixes. It must never
  contain a step that can change behaviour or generate code.
- Its exit status is ignored. `validate` is the gate, and `autofix` is not.

## Rules every task must follow

These come from `AGENTS.md`. Each one has cost a live incident. `fabro validate` does
not catch any of them.

1. **`.fabro` files are Graphviz DOT.** A node's shell script lives in a `script="..."`
   attribute. Inside it:
   - The **only** backslash allowed is `\"` (an escaped double quote). Never write `\n`,
     `\t` or `\\`: the DOT parser turns `\n` into a real newline and breaks `printf` and
     `jq`. To get a newline inside a jq program, write `([10]|implode)`. To put several
     lines in a file, use several `echo` commands.
   - Every `"` inside the script is written `\"`.
   - Never write `#` comments inside a script. Fabro pastes the whole script into every
     later agent prompt, twice. Put explanations in `//` DOT comments **above** the node.
   - Scripts run under POSIX `sh`: no `[[ ]]`, no `pipefail`, no arrays, no `local`.
2. **Routing.** A command node that prints `{"context_updates":{...}}` **must** declare
   `output_schema="routing"`, or fabro silently ignores the output. A node that prints no
   routing object **must not** declare it, or the stage fails. The routing object must
   be the **last** JSON object printed.
3. **Every command node has an unconditional edge to the workflow's failure node** (in
   `backlog` that is `human_rescue`, and in the shared merge phase it is
   `mark_needs_human`). The success path is a **conditional** edge, usually
   `[condition="outcome=succeeded"]`.
4. **Delete a contract file before the stage that writes it runs.**
5. **Agents never run git.** Command nodes do.
6. **Stylesheet class names** use `[a-z0-9-]` only: `.ci-fix`, never `.ci_fix`.
7. **A class used in `_shared/review-merge/` needs a rule in both** the `backlog` and the
   `pr-review` `model_stylesheet`.
8. **Node timeouts in either root graph stay below `stall_timeout="60m"`.**
9. Never put a token, key or webhook URL in a tracked file. This repository is public.

## How to check your work offline (no server needed)

Run these from the root of the `fabro-workflows` checkout:

```sh
./ops/test-task-gates.sh                         # graph shell tests; prints "PASS: N checks"
( cd ops/scheduler && uv run pytest -q )         # scheduler tests; 389 passed before this series
python3.11 -c 'import tomllib,sys; [tomllib.load(open(p,"rb")) for p in sys.argv[1:]]' \
  .fabro/workflows/*/workflow.toml
```

`ops/test-task-gates.sh` printed `PASS: 184 checks` before this series. Each task that
adds checks raises that number. `fabro validate` and `ops/check-routing-schemas.py` both
need the `fabro` binary, which exists only in the container on the fabro host, so only
the operator runs them (task 11). Without them you cannot see a routing mismatch, so
follow rule 2 above exactly.

## Task index

| # | File | Where the work happens | Blocked by |
|---|---|---|---|
| 01 | `01-backlog-pushes-on-purpose.md` | fabro-workflows | — |
| 02 | `02-rerun-flaky-ci-once.md` | fabro-workflows | — |
| 03 | `03-ci-fix-on-glm-5-3-flash.md` | fabro-workflows | — |
| 04 | `04-autofix-node.md` | fabro-workflows | — |
| 05 | `05-writers-app-fix-sh.md` | **writers-app** repository | — (inert until 04 is deployed) |
| 06 | `06-lawncare-ci-parity.md` | **lawncare-saas** repository | — |
| 07 | `07-wfs-backend-suite-issue.md` | **womens-fantasy-sports** issue | — |
| 08 | `08-scheduler-priority-label.md` | fabro-workflows `ops/scheduler/` | — |
| 09 | `09-scheduler-remainder-promoter.md` | fabro-workflows `ops/scheduler/` | 08 |
| 10 | `10-task-budget-and-remainder.md` | fabro-workflows | 09 |
| 11 | `11-validate.md` | operator, on the fabro host | 01, 02, 03, 04, 10 |

Tasks 01–08 can run in parallel. Tasks 01 and 02 both edit
`.fabro/workflows/_shared/review-merge/review-merge.fabro`, but in different nodes, so
rebase the second one onto the first.

## Deploy notes (operator)

- Graph and prompt changes (01, 02, 03, 04, 10) go live on the next run after they reach
  `main`. Nothing is copied.
- Scheduler changes (08, 09) need
  `rsync … ops/scheduler/ andrew@10.10.0.32:~/fabro/scheduler/` and then
  `docker compose up -d --build scheduler` (see `AGENTS.md`).
- **Deploy 09 before 10 reaches `main`.** A remainder issue filed before the promoter
  exists is never labelled `agent`. It is visible on GitHub, but nothing works on it.
- Task 06 changes what `validate` runs in lawncare-saas. Rebuild
  `fabro-python-node:local` with `ops/profile-images/build-images.sh` after it merges.
