# B4 · Shared code context: stop every agent from grepping cold

**Status:** proposed. **Axis:** throughput. **Effort:** M. **Feasibility:** high, with no
service to run. **Depends on:** a profile-image rebuild (tree-sitter grammars).

## Problem

In one `backlog` run, `decompose`, `improve`, `coder`, the per-task `review`, three
whole-branch reviewers and the merge-phase reviewers each rebuild the same picture of the
repository with `grep`/`glob`/`read_file`. The only thing handed between them is the file list
in a task. Evidence:

- `improve` is 27% of box time (p50 7.6 min on the box against about 3 min on `glm-5.3`), and
  it re-reads the checkout every turn (`backlog/workflow.fabro`, the comment above `improve`).
- 121 `agent.loop.detected` events in 8 runs on 2026-09-24, all repeated `read_file` loops.
- Prefill on a llama.cpp box is a real share of turn latency (`backlog/workflow.fabro`, the
  `default_fidelity` comment).

## Constraint that shapes the design

An LSP-backed server such as Serena cannot be reached over MCP here: the `sandbox` transport
needs Daytona, and `stdio`/`http` servers cannot see the run's working tree (see `00-overview.md`,
settled constraints). The context layer is therefore **files in the sandbox**, produced by
command nodes and read by agents.

## Design, in three steps

### Step 1: measure (S)

Using fabro's insights SQL, per stage over the last 50 runs: tool calls by name, the share of
input tokens that came from `read_file`/`grep` results, and repeated reads of the same path in
one stage. This is the number the next two steps must reduce. If exploration is under ~15% of
tokens, stop after step 3.

### Step 2: a repo map in `prep` (M)

A script in the profile images, `fabro-repomap`, following Aider's repomap design (tree-sitter
tags, a reference graph, PageRank, truncated to a token budget):

- It emits `/tmp/fabro/repomap.txt`: files ranked by centrality, each with its top-level
  symbols and one-line signatures, capped at about 4k tokens.
- `prep` runs it once, and `next_task` re-runs it after each task lands, because the tree
  changes. It takes seconds on these repos.
- The images carry the **grammars** (Python, TypeScript/TSX, Rust). The source is read at run
  time, because `build-images.sh` deliberately copies no source.
- Every agent prompt's input list gains the map as its first item: "read this before you
  search."

### Step 3: a per-task dossier from `improve` (S)

`improve` already reads the current checkout and decides whether the task is ready, redundant
or needs splitting. Have it also write `/tmp/fabro/task-context.md`:

- the files and symbols the task touches, with line ranges;
- the existing test file to extend, and the command that runs it;
- the governing passage of `CONTEXT.md` or the ADR, by path;
- known traps ("`useSSE` is also imported by X").

`coder`, `rework_*`, `review` and the per-task reviewers read the dossier before exploring.
`next_task` deletes it at the start of each task (the delete-before-write invariant). One agent
pays the exploration cost, and the others inherit it.

## Considered and not recommended now

- **Serena / LSP over MCP:** no transport (above). Revisit if fabro ships a Docker-capable
  sandbox MCP transport. Check again on each upgrade.
- **A host-side code index over `main`, served by HTTP MCP:** buildable, but stale against the
  run branch from task 2 onward, and the repo map covers orientation. Revisit if step 1 shows
  cross-file reference lookups dominate.
- **GitNexus:** license (PolyForm Noncommercial).
- **Baking the map into the image:** the images carry no source, and the map would be stale
  against the run's own branch.

## Verification

Re-run step 1's query over the 20 runs after steps 2 and 3 land. The target is a lower
exploration share and fewer `agent.loop.detected` events, with no drop in auto-merge rate (B1).
