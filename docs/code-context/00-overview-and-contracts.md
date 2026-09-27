# Shared code context: overview and canonical contracts

**Status:** tasks 01–07 applied 2026-09-26 (`17f2070`), and live on `main`. A review the
same day ran the wrapper against the four target repositories and rewrote it to read the
index database directly (C2, ADR 0014 D1 defect 3). Task 08: images built and the change
pushed; the first-run watch and the measurement are pending. All operator decisions are
made. They are listed in "Decisions" at the end of this file.

**Read this file first.** Every task in this folder assumes the decisions, contracts and
rules in this file. Each task repeats what it needs. If a task and this file disagree, this
file is correct. Stop and report the conflict. Do not guess.

Source decision: `docs/adr/0014-shared-code-context.md` (ADR 0014). Roadmap record:
`docs/factory-roadmap/B4-shared-code-context.md`.

**When you write or change a `.fabro` file or a `workflow.toml`, invoke the
`/fabro-workflow` skill first.** This file adds only the facts that are specific to this
change.

## What this is

Agents spend 58% of their tool calls and about 39% of their input tokens exploring the
checkout (ADR 0014, "Evidence"). This series gives every run of every workflow a **Code index**: a
codegraph database that `prep` builds inside the sandbox. Agents query it through the
`shell` tool with `fabro-code`. Two files derived from it are handed to agents: the **Repo
map** and the **Task dossier**.

```
claim → prep (+ fabro-code index, + fabro-code map) → decompose → … → next_task (+ map again)
      → improve (+ writes task-context.md) → coder / rework_* (read map + dossier, query fabro-code)
```

The same one-line build goes into the entry node of `pr-review` (`claim`), `arch-review`
(`prep`) and `issue-triage` (`acquire`), in task 05. No node is added. No edge changes.
The consumer prompts gain an input and a paragraph.

## Glossary (from `CONTEXT.md`)

- **Code index**: the run's own queryable graph of the checkout's symbols, calls and imports.
  It is built from the run's working tree and never from `main`.
- **Repo map**: a ranked, size-capped outline of the checkout, rendered from the Code index,
  and handed to agents as a file.
- **Task dossier**: the facts that `improve` found about one task, written for the stages
  that implement and review it.

## Canonical contracts

### C1. Files

| Path | Written by | Read by | Lifetime |
|---|---|---|---|
| `.codegraph/` (in the checkout) | `fabro-code index`, and `sync` on each query | `fabro-code` only | whole run; excluded from git (C4) |
| `/tmp/fabro/code_index` | `fabro-code index` | `fabro-code`, `fabro-code map` | whole run. One line: `ok` or `failed: <reason>` |
| `/tmp/fabro/repomap.md` | `fabro-code map` in `prep` and `next_task` | agent prompts | overwritten on each task |
| `/tmp/fabro/task-context.md` | the `improve` agent | `coder`, `rework_*`, `review` | deleted by `next_task` at the start of each task |

### C2. `fabro-code` (installed at `/usr/local/bin/fabro-code` in every profile image)

POSIX `sh`, `jq` and `sqlite3`. Source: `ops/profile-images/fabro-code`. The exit code is 0
on an answer, 1 when the answer is "nothing found" or "ambiguous", 2 when there is no
usable index, and 64 on a usage error.

codegraph builds and syncs the index. Every answer is read from
`.codegraph/codegraph.db` with `sqlite3 -readonly -json`. codegraph's query verbs
(`query`, `node`, `callers`, `callees`, `impact`, `affected`) are never called: each one
re-resolves a bare name fuzzily, and each walks guessed edges (ADR 0014 D1, defect 3).

| Verb | Does |
|---|---|
| `index` | When `.codegraph/` is missing, `codegraph init --yes`. When it exists, `codegraph sync`. Both have a 120 s timeout. Writes `/tmp/fabro/code_index`: `ok` only when the command succeeded **and** the database exists. Never exits non-zero. Safe to call more than once. |
| `def <name>` | The symbol whose name is exactly `<name>`: `path:line  kind  name  signature`, on one line |
| `show <name\|path:line>` | One exactly resolved symbol (or the narrowest symbol holding `path:line`): its source (150 lines at most), then `## calls` and `## called by` (15 each), then the footer |
| `callers <name>` / `callees <name>` | Trusted `calls` and `instantiates` edges into or out of the resolved node, one `path:line  kind  name` per call site (a module-level call site is the file node), plus the footer |
| `impact <name>` | Every node that reaches the resolved node through at most 2 trusted incoming edges (`calls`, `instantiates`, `references`, `extends`, `implements`, `imports`), plus the footer |
| `tests <path>...` | Test files that import the given files, directly or through up to 4 more files (trusted `imports` edges), nearest first. A test file is one that matches `TEST_RE`: `test_*.py`, `*_test.py`, `*.test.*`, `*.spec.*`, `*_test.go`, `__tests__/`, `tests/*.rs` |
| `map` | Writes `/tmp/fabro/repomap.md` (C3) |

A **definition** is any node except `field`, `property`, `parameter`, `enum_member`,
`import`, `export` and `file`. A `variable` or `constant` counts only at the top level of a
file, because TS declares most functions, components and hooks as
`export const X = () => ...`. Inside a class or a function it is a member or a local.

A **trusted edge** is not `fuzzy` (`metadata.resolvedBy`), and its two ends are in the
same language family (`typescript`, `tsx`, `javascript` and `jsx` are one), unless it was
resolved as `framework`.

Rules that every verb follows:

1. Every query verb runs `codegraph sync --quiet` first. The answer covers uncommitted edits.
2. Name resolution is **exact**, against definitions only. If zero symbols match, print
   `no symbol named <name> in the index (fields and attributes are not indexed); use grep -rnw`
   and exit 1. If two or more match, print each one as `path:line  kind  name` and exit 1.
   Never choose among them.
3. `show`, `callers`, `callees` and `impact` end with this line:
   `graph edges are calls and imports only, and a call is matched by name, so a same-name call on another type can appear; confirm with grep -rnw before deleting or renaming`.
4. If `/tmp/fabro/code_index` is not `ok`, or the database is missing after the sync, every
   verb prints `no code index; use grep` and exits 2. An unknown schema version, or a
   failed query, also exits 2. A broken index never reads as "nothing found".
5. The output is text. It is capped at 200 lines, and the last line says how many lines were
   cut.

### C3. Repo map format

Markdown, at most 16,000 bytes (about 4k tokens). The first line is
`# Repo map (<short HEAD sha>, <file count> files indexed)`. Then one block per file, ranked
by the count of trusted `calls`, `imports`, `references`, `extends` and `instantiates`
edges into the file's symbols **from other files**. Rows where `files.generated = 1` are
excluded, and so are test files (`TEST_RE`) and test support: any path under a `test/`,
`tests/`, `__tests__/`, `__mocks__/` or `fixtures/` directory, and `conftest.py`. Those
top every ranking without being the code a task changes. Each block is `## <path>`
followed by up to 8 top-level definitions, ranked the same way, as
`- <kind> <name><signature>`, on one line each: whitespace in a signature is collapsed and
a signature is cut at 100 characters. The rows come back from `sqlite3` as JSON, so a
signature may contain `|` or newlines. Stop adding files when the next block would pass the
cap.

### C4. Keeping the index out of git

`prep`, before `fabro-code index`:
`grep -qx '.codegraph/' .git/info/exclude 2>/dev/null || echo '.codegraph/' >> .git/info/exclude`.
The test suite proves this with real git: after an index build, `git add -A && git status
--porcelain` does not show `.codegraph`.

### C5. Environment in every profile image

`CODEGRAPH_TELEMETRY=0`, `DO_NOT_TRACK=1`, `CODEGRAPH_NO_UPDATE_CHECK=1`. codegraph 1.6.0
from `codegraph-linux-x64.tar.gz`, with sha256
`de3391f79ed42622d937e6cd5b7642a7ea8bb7d1473607e80b879ba73ef216b0`, unpacked to
`/opt/codegraph`, and `/usr/local/bin/codegraph` is a symlink to it. `sqlite3` from apt.

## Rules this series inherits

From `AGENTS.md`, the deployment invariants. These rules apply most often here:

- `\"` is the only backslash in a `.fabro` file, and no `#` goes in a `script=`. The one line
  added to each script contains neither.
- A node that writes before `prep` is staged against a missing directory in
  `ops/test-task-gates.sh`. No node here does, but `prep`'s new lines get a section.
- Delete a contract file before the agent that writes it runs (`task-context.md`).
- Agents do not run `git`. `fabro-code` runs no git command that changes state.
  `codegraph sync` reads file stamps, not git.

## Tasks

| # | File | Where | Depends on |
|---|---|---|---|
| 01 | `01-measure.md` | `ops/fabro-exploration-share.py` | — (first, so the baseline and the result use one script) |
| 02 | `02-profile-images.md` | `ops/profile-images/` | — |
| 03 | `03-fabro-code-wrapper.md` | `ops/profile-images/fabro-code`, `ops/test-task-gates.sh` | 02 |
| 04 | `04-prep-and-next-task.md` | `backlog/workflow.fabro` | 03 |
| 05 | `05-other-workflows.md` | `pr-review`, `arch-review`, `issue-triage` entry nodes | 04 |
| 06 | `06-prompts.md` | phase-1 prompts in `backlog/`, `_shared/`, `arch-review/` | 05 |
| 07 | `07-task-dossier.md` | `improve.md.j2`, `next_task`, consumer prompts | 06 |
| 08 | `08-validate-deploy-verify.md` | host | 01–07 |

## Decisions

1. **codegraph is the index** (ADR 0014 D1), and graphify is the documented fallback.
   Accepted 2026-09-26.
2. **Phase-1 prompts** (ADR 0014 D6): `decompose`, `improve`, `coder`, `rework_*`,
   `review_fix`, `arch-review` `scan`, and the shared triage `improve` and `triage`.
   Accepted 2026-09-26.
3. **The dossier is optional, permanently.** `improve_gate` never checks it. The measurement
   script reports its presence rate every time it runs. Accepted 2026-09-26.
4. **Build the index wherever there is a checkout**: all four workflows (task 05). Prompts
   adopt it in two phases (ADR 0014 D6). Accepted 2026-09-26.
5. **Success targets:** the table in task 08, measured after at least 30 `improve` and 30
   `coder` visits since deploy. Accepted 2026-09-26.
