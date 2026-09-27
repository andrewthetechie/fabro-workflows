# Agents query a code index in their own sandbox, built by codegraph, before they grep

**Status:** proposed (2026-09-26). Implementation plan: `docs/code-context/`. Roadmap item
`docs/factory-roadmap/B4-shared-code-context.md`. Operator decisions of 2026-09-26: codegraph
accepted (D1). Build the index in every workflow that has a checkout (D3). The prompts adopt it in two phases (D6). The Task dossier is optional permanently (D4).
Success is measured after 30 `improve` and 30 `coder` visits, against the targets in
`docs/code-context/08`. Tasks 01–07 applied 2026-09-26 (`17f2070`). A review the same day
ran the wrapper against the four target repositories and found defect 3 below; the
wrapper now reads the index database directly (D1). Task 08's measurement is pending.

Every agent stage in a `backlog` run builds its own picture of the repository with `grep`,
`glob` and `read_file`. Nothing it learns passes to the next stage. We add a **Code index**
to each run: `prep` builds it inside the sandbox from the run's own working tree, and agents
query it through fabro's built-in `shell` tool with a small wrapper CLI, `fabro-code`. The
index comes from **codegraph** (MIT), pinned by version and checksum. A **Repo map** that is
rendered from the index, and a **Task dossier** that `improve` writes, give the same facts to
the agents without a query.

## Evidence (B4 step 1, measured 2026-09-26)

These numbers come from the 9 completed `backlog` runs since the 0.362 upgrade (5242 tool
calls; `fabro events --json`; the scripts are in `.scratch/b4-code-context/`):

- Exploration (repo `read_file`, `grep`, `glob`, and search or read through `shell`) is **58%
  of tool calls**, about **39% of input tokens** (estimated from retained result bytes), and
  **52% of agent turn time**. B4's stop rule was "under ~15%". The measured share is far
  above that.
- In `coder`, 53% of `read_file` calls read again a file that the stage already read, mostly
  to page through it at a different offset.
- The searches ask for **usages or callers of a named symbol (~38%)**, **definitions (~22%)**
  and **tests (~19%)**. Concept searches are ~3%. The same symbol is looked up again in
  `decompose`, `improve`, `spec` and `quality` in one run.

So the need is exact symbol and reference lookup. Semantic search is not the need.

## D1. codegraph, behind a wrapper, is the index

We compared seven tools with compatible licenses (GitNexus is excluded by license). We read
the primary sources, and we benchmarked the three that fit in a 2 CPU / 4 GB container on the
four target repositories:

| Tool | License | Result |
|---|---|---|
| **codegraph** 1.6.0 | MIT | **Chosen.** Tree-sitter call, import and reference graph in SQLite. No embeddings. Cold index 1.2–4.3 s, 220–515 MB RSS, on all four repositories. Incremental `sync` 0.6 s. JSON output on `query`, `callers`, `callees`, `impact`, `affected`. The runtime is bundled (62 MB tarball). |
| graphify | Apache-2.0 | Fallback. Better recall (imports and calls), and it refuses an ambiguous name. But `update` takes the same time as a full build (25 s), `graph.json` is 19.5 MB, queries print only text, it needs Python in every image, and it has 1,475 open issues. |
| gortex | Apache-2.0 | Rejected. It is built around a daemon. A query takes a graph id, not a name. Cold index 28 s. The symbol lookup gave a wrong fuzzy match. |
| semble | MIT | Rejected. Semantic search only, with no graph. Concept search is 3% of the need. |
| CodeGraphContext | MIT | Rejected. Heavy dependencies, `update` is a full reindex, table output only, and its default embedded DB has a 4 GiB buffer pool. |
| grepai | MIT | Rejected. It cannot index without an embedding server (operator decision: no network dependency in the sandbox). |
| mcp-vector-search | Elastic-2.0 | Rejected. Not OSI, pulls in torch, alpha quality. |

codegraph has two defects that the benchmark found. The wrapper exists because of them:

1. **Its name lookup matches fuzzily.** `codegraph node LIVE_SCORING_ENABLED` returned an
   unrelated method, `readScores`. `fabro-code` resolves a name only by an **exact** match on
   the symbol name. If there is no exact match, it says so. If two or more symbols match,
   it lists each one with its path and does not choose.
2. **`callers` means call edges, not all usages.** Against a `grep -w` baseline, it found
   1 of 18 files for a settings field, 20 of 50 for an enum, and 15 of 37 for a function.
   None of the three tools benchmarked indexes a field read such as `settings.X`. Every
   `fabro-code` answer about references ends with one line: the graph covers calls and
   imports only, so confirm with `grep -rnw` before you delete or rename. The index
   replaces grep for **orientation and definitions**. It does not replace grep as **proof
   of absence**. (Its `callers` also stops at 20 results unless given `--limit`, so the
   "20 of 50" above is at least partly that default.)
3. **Its edges include guesses, and every query verb walks them.** Each edge records how
   it was resolved (`metadata.resolvedBy`). On womens-fantasy-sports 633 `fuzzy`
   (confidence 0.3) call edges run from Python `select(...)` to the TSX `Select`
   component, so `codegraph callers Select` answered 792 callers where one is real, and
   `affected` listed 152 backend Python tests for one frontend file. jelly-swipe's TSX
   tests "call" a Python `render` method by exact name. So `fabro-code` never uses
   codegraph's query verbs. It reads `.codegraph/codegraph.db` with `sqlite3`, resolves a
   name to one node id, and starts every graph answer from that id. It counts an edge only
   when it is not `fuzzy` and joins one language family (ts, tsx and js are one family),
   unless it was resolved as `framework` (writers-app's TS types that mirror Rust structs).
   A same-name call inside one language is still counted (`client.cookies.get` is a
   "caller" of `TmdbCache.get`), and the footer says so. codegraph only builds and syncs the
   index, and the gate suite pins the schema version the wrapper reads.

We pin the version and the tarball's sha256 in the profile images. `codegraph upgrade`
never runs. A version bump is a deliberate change: re-run the bench in
`.scratch/b4-code-context/bench/` and the gate suite. The project has one main maintainer,
and its star count grew very fast, so we trust the pinned binary and not the upstream
project.

## D2. The index is a CLI in the sandbox, not an MCP server and not a host service

Fabro's `sandbox` MCP transport still needs Daytona (checked on upstream `main`, 2026-09-26).
A `stdio` or `http` server cannot see the run's working tree. But every agent has the `shell`
tool, and `shell` runs in the sandbox. So a CLI in the profile image reads the live tree with
no MCP. This is the model that GitNexus fits, without GitNexus.

A host-side index of `main` is stale from the run's first task onward. The index in the
sandbox is never stale: **every `fabro-code` query runs `codegraph sync` first** (0.6 s), so
it sees the agent's own uncommitted edits.

## D3. Every workflow's entry node builds the index synchronously, and a build failure never fails the run

The operator allowed up to 5 minutes, built alongside the early stages. That is not
necessary: the slowest cold build measured is 4.3 s on 2 CPUs. So the entry node builds it
inline, with no fan-out and no background process.

The operator's rule is "use the index wherever we can". All four workflows run in the same
per-repository profile images, so each of them builds the index where its checkout first
exists: `backlog` `prep`, `pr-review` `claim` after `gh pr checkout`, `arch-review` `prep`,
and `issue-triage` `acquire`. `fabro-code index` is idempotent: it builds the index when
there is none and syncs it when there is one. So the shared phases (`_shared/review-merge/`,
`_shared/triage/`) find an index in every graph that imports them, and they build nothing
themselves. (If a background process survives the end of a
command node in fabro's docker sandbox, that is not verified. This design does not depend on
it.)

The index makes agents faster. It is not needed for correctness. If `codegraph` is
missing, fails or times out, `prep` records that, the wrapper prints `no code index; use
grep`, and the run continues as it does today.

## D4. Push and pull: a repo map, a task dossier, and the query CLI

- **Repo map** (push): `/tmp/fabro/repomap.md`. The files are ranked by inbound edges, each
  with its top-level symbols and signatures, generated files are excluded, and the map is
  capped at about 4k tokens. `fabro-code map` renders it from the index. `prep` writes it,
  and `next_task` writes it again after the mainline refresh. It is the first input in every
  prompt that gets it. It follows the Aider repomap design from B4 step 2, with codegraph's
  graph as its source instead of a separate tree-sitter pass.
- **Task dossier** (push): `/tmp/fabro/task-context.md`, written by `improve` (B4 step 3).
  It lists the task's files and symbols with line ranges, the test file to extend and the
  command that runs it, the governing `CONTEXT.md` or ADR passage, and known traps.
  `next_task` deletes it at the start of each task (the delete-before-write rule). It is
  optional: no gate checks it, because each miss that a gate rejected would cost a full
  `improve` re-run on a coder box. The measurement reports how often it is present.
- **`fabro-code`** (pull): `def`, `show`, `callers`, `callees`, `impact`, `tests`, `map`. It
  prints compact text, one `path:line  kind  name  signature` per line. It does not print
  JSON, because a small local model reads text better, and text costs fewer tokens. `show`
  prints a symbol's source with its call trail. That replaces paging a large file through
  `read_file`.

## D5. The index stays out of every commit

`.codegraph/` is in the working tree, and fabro's checkpoint runs `git add -A` after every
stage. `prep` adds `.codegraph/` to `.git/info/exclude` before it builds the index. That keeps
it out of checkpoints, and so out of the PR diff, the hygiene counters (ADR 0013),
`open_pr_prep`'s empty-diff floor and the `.github/workflows/` push check. The images set
`CODEGRAPH_TELEMETRY=0`, `DO_NOT_TRACK=1` and `CODEGRAPH_NO_UPDATE_CHECK=1`, and nothing runs
`codegraph install`. That command would write a section into the target repository's
`AGENTS.md`.

## D6. The prompts adopt it in two phases

The index is in every sandbox from the start. Only the prompt text is phased, so that the
first measurement has few variables:

- **Phase 1:** `backlog` `decompose`, `improve`, `coder` and `rework_*`, and the shared
  `review_fix`. These are the stages with the most exploration (`decompose` and
  `review_fix` are 58% each). Also `arch-review` `scan` and the shared triage `improve` and
  `triage`, which the `backlog` measurement does not count.
- **Phase 2:** after the phase-1 measurement, the remaining reviewers: `review`,
  `standards`, `spec`, `quality`, `refute`, `extra_decompose`, `ci_fix`,
  `resolve_merge`, and `pr-review` `rebase`.

## Considered and rejected

- **Build the index in parallel with `decompose`, and block only where it is needed.** The
  operator asked for this. It is not needed at 4 s, and it would need either a fan-out
  around `decompose` or a detached process whose survival across command nodes is not
  verified.
- **Semantic search, local or on a LAN embedding endpoint.** 3% of the measured need. A LAN
  endpoint would also add an unbounded network wait to a stage (the ADR 0007 lesson).
- **Replacing grep.** The recall numbers in D1 make that unsafe. Grep stays the proof of
  absence.

## Consequences

- Every profile image carries about 62 MB more, plus `sqlite3` for the map renderer.
- The map renderer reads codegraph's SQLite schema (`nodes`, `edges`, `files.generated`).
  A version bump can break it. The gate suite covers the renderer.
- The Refuter (ADR 0013) may use `fabro-code`. The index is derived from the code. It is not
  another agent's output, so the Refuter stays independent.
