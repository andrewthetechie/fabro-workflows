# C3 · Parallel hosted reviewers

**Status:** proposed (rev 2 "Lever B"). **Axis:** throughput. **Effort:** M.
**Feasibility:** medium. Verify the shared-working-tree question first.

## Problem

After the task loop, `backlog` runs `standards` → `spec` → `quality` (whole-branch reviewers,
hosted, 15-minute timeouts each) one after another while the leased box idles. The merge
phase does the same with `standards` → `spec`. Wall-clock per round is the sum. It could be
the maximum.

## Design

A fabro `component` fan-out into the three reviewer+gate pairs, and a `tripleoctagon` fan-in
that joins them, then `extra_prep` as today.

- **Not `for_each`.** It takes one context array, one outgoing edge and one cloned prompt. Three
  different prompts need a component fan-out (rev 2 correction 8).
- `max_parallel` defaults to 4, and the join waits for all branches.
- The reviewers are read-only and write distinct files under `/tmp/fabro/review/`, but the
  branches **share one sandbox working tree**. Confirm with fabro's source for the installed
  version that parallel branches do not checkpoint (`git add -A && git commit`) concurrently.
  If they do, the checkpoint of one branch can race another's.
- Fidelity: on a fork-to-branch edge, explicit `full` degrades to `summary:high`. Confirm that
  `truncate`, the graph default and an `AGENTS.md` invariant, survives the fork unchanged.

## Gain

About 20–30 minutes per review round, measured from the stage durations in B1. It does not
change box utilisation: the box idles either way.

## Sequencing

After `docs/fabro-upgrade/`, because parallel-branch behaviour is engine code that the upgrade
may change.
