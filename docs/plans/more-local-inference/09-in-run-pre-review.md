# Task 09: in-run box pre-review

Read `00-overview-and-contracts.md` first (D2, D10, D21). **Invoke `/fabro-workflow`**, then
read `docs/agents/invariants-graph.md` and `invariants-backlog.md` in full. Read backlog's
`validate`, `review`, `standards`, `quality` and `spec` nodes and their gates, and the edges
around `validate` (`validate -> review` requires `context.task_validation_ok=true`).

**Start only when the overview's Status line records Phase 3 as passed, and records either
the ralph trial's verdict (ADR 0029) or that the operator accepts the confound** (D21). This
task changes `backlog` only, so during the trial it would move one arm.

## Goal

After the coder finishes a task, the box runs the same review rubric on its own work and fixes
what it finds, before any hosted reviewer sees it. Fewer hosted rejections mean fewer hosted
review and rework visits. This is not a background job: it runs on the coder loop's own lease,
so it fills no gap but costs no credits.

## Steps

1. **`backlog/workflow.fabro`:** a `pre_review` agent node, visited **once per task**, between a
   green `validate` and `review`.
   - Class `.coder`, so it is on the box with `reasoning_effort: medium`.
   - Its prompt reuses `reviewer.md.j2`'s rubric and adds: fix every error-severity finding
     yourself, run the task's checks, and submit a short list of what you changed.
   - Timeout 30m, `max_retries=0`.
2. **Once per task.** A small command node in front of it, `pre_review_prep`, records the
   current HEAD in `/tmp/fabro/pre_review_base` and the task index in
   `/tmp/fabro/pre_reviewed_task`. Routing:
   - `validate` green, and `pre_reviewed_task` is not the current task index →
     `pre_review_prep` → `pre_review` → `validate`;
   - `validate` green, and `pre_reviewed_task` is the current task index → `review`, as today.
   - Publish the comparison as a context value from `validate` (or from a routing node after
     it) with `output_schema="routing"` (R8), and keep an unconditional fallback edge (R12).
   - A rework round of the same task does not pre-review again; the next task does.
3. **The pre-review may only help.** If `pre_review` fails, or the second `validate` is red,
   a command node `pre_review_revert` resets the tree to `/tmp/fabro/pre_review_base`
   (`git reset --hard`, then remove untracked files the stage created) and routes to `review`
   with the coder's own work. Never route a pre-review failure to `rework_router`.
4. **Stage manifest:** `pre_review` with `"box": true` and its inputs. R13 and R15 cover the
   claim.
5. **`ralph` is not changed:** its trial arm is compared as built (ADR 0029).
6. **Node and edge baselines:** update `AGENTS.md`'s `Backlog` counts and the `check-host`
   table in the same commit, and check R1 (the breaker) still holds with the new cycle.
7. **Gate tests** for `pre_review_prep`, the routing and `pre_review_revert`: first visit,
   second visit of the same task, a new task, and the revert restoring the tree.

## Done when

- `make check` passes, with the new baseline recorded.
- Commit: `feat(backlog): a box pre-review before the hosted per-task review (more-local-inference 09)`.
