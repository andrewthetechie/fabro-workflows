# Task 09: in-run box pre-review

Read `00-overview-and-contracts.md` first (D2, D10). **Invoke `/fabro-workflow`**, then read
`docs/agents/invariants-graph.md` and `invariants-backlog.md` in full. Read backlog's
`review`, `standards`, `quality` and `spec` nodes and their gates.

**Start only after task 12's Phase 3 exit check has passed.**

## Goal

After the coder finishes a task, the box runs the same review rubric on its own work and fixes
what it finds, before any hosted reviewer sees it. Fewer hosted rejections mean fewer hosted
review and rework visits. This is not a background job: it runs on the coder loop's own lease,
so it fills no gap but costs no credits.

## Steps

1. **`backlog/workflow.fabro`:** a `pre_review` agent node between `validate` (task validation
   passed) and `review`.
   - Class `.coder`, so it is on the box with `reasoning_effort: medium`.
   - Its prompt reuses `reviewer.md.j2`'s rubric and adds: fix every error-severity finding
     yourself, run the task's checks, and submit a short list of what you changed.
   - Timeout 30m, `max_retries=0`.
   - On failure it routes to `review` unchanged: the pre-review may only help, never block.
2. Re-validate after it: an edge back through `validate`, so a pre-review edit that breaks the
   task never reaches the hosted reviewer.
3. **Stage manifest:** the new stage with `"box": true` and its inputs. R13 and R15 cover the
   claim.
4. **`ralph` is not changed:** its trial arm is compared as built (ADR 0029).
5. **Node and edge baselines:** update `AGENTS.md`'s `Backlog` counts and the `check-host`
   table in the same commit.
6. **Gate tests** for any new command node.

## Done when

- `make check` passes, with the new baseline recorded.
- Commit: `feat(backlog): a box pre-review before the hosted per-task review (more-local-inference 09)`.
