# Task 09: in-run box pre-review

**Status (2026-10-31): SUPERSEDED by `pre-review-and-calibration` Part B.** The pre-review was
built, in `backlog` and `ralph`, **once per run on the whole branch** — not per task, and not in
`backlog` only — and behind an issue-number A/B arm. The build is commits `25304e4` (the nodes, the prompt and the
schema) and `5e02681` (the docs), with the review's fixes on top; the rules are the
pre-review rows in `docs/agents/invariants-backlog.md` and `invariants-ralph.md`, and the
graph comment above `pre_review_check` in `backlog/workflow.fabro`. The rest of this file is kept
as the record of what it said and why the placement changed; step 1-7 below are no longer the
task.

Read `00-overview-and-contracts.md` first (D2, D10, D21). **Invoke `/fabro-workflow`**, then
read `docs/agents/invariants-graph.md` and `invariants-backlog.md` in full. Read backlog's
`validate`, `review`, `standards`, `quality` and `spec` nodes and their gates, and the edges
around `validate` (`validate -> review` requires `context.task_validation_ok=true`).

## Goal

After the coder finishes a task, the box runs the same review rubric on its own work and fixes
what it finds, before any hosted reviewer sees it. Fewer hosted rejections mean fewer hosted
review and rework visits. This is not a background job: it runs on the coder loop's own lease,
so it fills no gap but costs no credits.

## Why the placement changed

The goal is right and the placement was wrong. A per-task pre-review sits in front of the
**per-task** hosted review, and that review almost never sends work back — while the hosted
spend and the merge holds are in the **whole-branch** reviewers. Token totals since 2026-10-21
(scheduler DB, `run_stage_stats`):

| Workflow | Stage (hosted unless noted) | Runs | Visits | Input tokens |
|---|---|---|---|---|
| backlog | per-task `review` (glm-5.3-flash) | 113 | 372 | 14.7M |
| backlog | `rework_t1` (box) / `rework_t2-4` (hosted) | 16 / 7 | 16 / 9 | 7.4M / 0.4M |
| backlog | extra review: `standards`, `spec`, `quality` | ~100 | ~413 | 28.0M |
| backlog | `extra_decompose` (box) → follow-up tasks | 99 | 135 | 5.0M |
| backlog | merge phase: `review_merge.standards`, `.spec`, `.review_fix` | 98 | 298 | 21.6M |
| backlog | `review_merge.refute_fix` (box) | 50 | 50 | 5.1M |
| ralph | `standards`, `spec` (whole branch) | 75 | 176 | 13.9M |
| ralph | `refute` | 80 | 124 | (gpt-6-luna; input not metered here) |

351 coder visits against 372 per-task reviews: a per-task review rarely sends work back (16
tier-1 reworks in 113 runs). The hosted spend and the merge holds are in the whole-branch
reviewers. In backlog their findings become follow-up tasks (`extra_decompose`), each of which
costs another plan, coder and review cycle; in the merge phase and in ralph's review, findings
left unfixed are what holds a PR. On 2026-10-30/31 `writers-app#1284` was held because
"standards findings, spec findings, 0 fixed by the loop and 2 left", and across all PRs "There
are unfixed findings; a human must merge this PR" is the second most common hold reason
(`pr_facts.reason`, 23 PRs).

So the pre-review runs **once per run, on the whole branch**, after the last task and before the
hosted whole-branch reviews:

- **backlog:** `next_task -> pre_review_prep [tasks_done && lane!=fast]`, then
  `pre_review_prep -> pre_review -> pre_review_check -> extra_prep`. The extra review,
  `extra_decompose`'s follow-up tasks, Final validation (`open_pr_prep`) and the merge phase all
  see the pre-reviewed branch; Final validation is the full net.
- **ralph:** `pick -> pre_review_prep [loop=done && phase=pre]`, the same three nodes, ending at
  `final_merge`. **Before `final_check`**, never after: `final_check` writes
  `/tmp/fabro/validated_tree`, which is the Refuter's "validation passed on this exact tree"
  evidence. An edit after it would make every command criterion `unverifiable`.

The trial's "wait for the ralph verdict" condition is withdrawn (operator decision 2026-10-31):
the stage is built in **both** arms, and the confound is handled by the A/B arm below instead.

## The A/B arm

Pre-review only when **bit 2** of the issue number is set (`(issue_number / 4) % 2 == 1`):
issues 4-7, 12-15, 20-23, and so on. The arm is published as `pre_review_arm`, and the
`pre_review_check` verdict as `pre_review_verdict`, on every visit, and the scheduler's
`run_summary` keeps both (`pre_review_arm`, `pre_review_verdict`), so `fabro events` and the
run summary say which arm a run was in and what its pre-review did. The switch is one line in
`pre_review_prep`, in both graphs: set `ARM=on` unconditionally to pre-review every run after
the measurement.

**It was `issue_number % 2 == 1` until 2026-10-31, and that was wrong.** Bit 0 is the ralph
trial's (`dispatch.choose_workflow` sends an even issue to ralph and an odd one to backlog), so
the pre-review ran on every `backlog` run and on no `ralph` run: it could not be measured
within `ralph` at all, and it skewed the trial it shared a bit with. Bit 1 is the box-plan
trial's arm (`issue_number // 2 % 2`). Three trials, three bits; never reuse one
(`.scratch/more-local-inference/background-jobs-plan.md`, the traps list). Runs between
2026-10-31 18:55Z and that fix's deploy used the old arm and are excluded from the measurement.

## How it will be measured (do not run this yet)

Compare the arms within each workflow, from `run_stage_stats` and `run_history`, over runs
dispatched after the deploy. Per run:

- hosted input tokens in `standards`, `spec`, `quality` and `review_merge.*` (backlog), and in
  `standards`, `spec` and `refute` (ralph);
- the number of `extra_decompose` follow-up tasks (backlog) and refuter task rounds (ralph);
- the merge rate, and the `pr_facts.reason` mix;
- the cost: `pre_review` box minutes and the lease's wall time.

About 30 runs per arm per workflow is two to three weeks at the current rate. The arm is on the
run's `run_history` row only through `workflow` and `issue_number` (odd or even), so join
`run_history.issue_number % 2` — there is no column for it and none is needed.

## What the original steps said, and where each landed

1. A `pre_review` agent node between a green `validate` and `review`, `.coder`, 30m,
   `max_retries=0`. **Kept as a node, moved to once per run** before `extra_prep` (backlog) and
   `final_merge` (ralph). `.coder`, 30m and `max_retries=0` are as written.
2. `pre_review_prep` recording HEAD and the task index, once per task. **Kept, changed:** it
   records the branch base (`/tmp/fabro/pre_review_base`), the diff and the arm, and latches on
   `/tmp/fabro/pre_reviewed` (once per run, not per task).
3. `pre_review_revert` on failure, `git reset --hard` and remove untracked files, never to
   `rework_router`. **Kept as `pre_review_check`,** which also decides whether to keep a
   successful pre-review: green `task-check` keeps, red or an error reverts with
   `git restore --source` plus `git clean -fd` (not `reset --hard`: the base is a commit, and a
   reset would drop a merge the run needs).
4. The Stage manifest, `"box": true`, R13/R15. **Done** — `backlog.pre_review` and
   `ralph.pre_review`, and `coder_box_stages` now lists `pre_review` for both, which is how the
   claim hook and the scheduler's background lane learn it is a box stage.
5. `ralph` is not changed. **Withdrawn** by the operator on 2026-10-31; it is built in both.
6. Baselines. **Done:** `Backlog` 84 nodes / 209 edges, `Ralph` 54 / 139.
7. Gate tests for the prep, the routing and the revert. **Done**, in
   `ops/gate_tests/test_pre_review.py`, for both graphs.

One thing the original did not have: the pre-review's findings must never reach the hosted
reviewers. Its prompt says its independence is the point; `pre_review.json` and
`/tmp/fabro/pre_review.log` are inputs of no other stage and are never appended to
`completed.md`.
