# Plan trial: a hosted planner in place of `improve` (#25)

**Status:** built 2026-10-13. The trial starts when it is deployed: the graph change goes live
on its push to `main`, and the scorecard split needs `make deploy-scheduler`. It ends with at
least 50 runs in each arm, which at about 30 runs a day is 4 to 5 days. The result goes in
`result-<date>.txt` next to this file, and the chosen shape in an ADR.

## The question

The operator works with an LLM this way: a stronger model writes a plan, then a smaller model
carries it out in a clean context. `backlog` already has half of that shape. The coder starts
a fresh session and reads `improve`'s output as files (`task-context.md`, and the code
`excerpts` copies from the ranges it cites). But `improve` runs on the run's own coder box,
the same model as the coder, and it writes facts (files, tests, traps), not a plan.

#25 measured `improve` at 24% of all stage time, as much box time as `coder`, for about 1.6
minutes of coder time saved per 10 minutes spent. Its proposal was to skip `improve` on half
of the tasks. The operator chose a different trial: **replace `improve` with a `plan` stage on
a hosted model** that does improve's job (check the task against the checkout, size it, split
it, cite line ranges) and also writes the edit plan the coder follows. If `plan` wins,
`improve` is deleted.

This reverses an earlier decision. ADR 0011 recorded "moving `.improve` back to a hosted model"
as declined on 2026-09-24. The operator reopened it as this trial on 2026-10-13.

## Design

| Decision | Choice | Why |
|---|---|---|
| D1. Planner model | `glm-5.3` (z.ai coding plan), no `reasoning_effort`, the same as `.decomp` | The operator's choice. There are already enough model families in the pipeline: glm-5.3 plans, DeepSeek codes on the box, glm reviews, and gpt-6.1-sol with hy4 or kimi-k3 refutes. A configuration already proven on this server. It is flat-rate, so the cost is counted in tokens and in z.ai's 3 concurrent sessions (ADR 0003), not in dollars. |
| D2. A new node beside `improve` | `plan`, class `.plan`, `prompts/plan.md.j2`, `timeout="20m"`, `max_retries=1` | Both arms run in the same graph at the same time, so time-varying factors (provider weather, Kimi's weekly quota, CI queue) hit both equally. The stage lists name the arm, so the scorecard needs no new run input. Ending the trial is one commit. |
| D3. The same contract as `improve` | `submit` to `improve_result.json` (schema `improve_result`), `improve_gate`, `excerpts` | The dispositions, split guards, task budget, revert backstop and receipt check are unchanged, so the comparison is only the planner. The manifest gives `plan` the same inputs as `improve`. |
| D4. The plan is a section of the dossier | `## Plan` and `## Done when` in `task-context.md`, cap 12,000 bytes (improve: 6,000) | `excerpts` copies every `path:start-end` in the file, so the plan's steps arrive with their code. The coder, rework and review prompts already read the file. |
| D5. Plan content | One step per edit with its range, intent and acceptance criterion; names, signatures and helpers; snippets of at most 15 lines for logic that is not obvious; never a whole file | The coder writes the code. A plan that writes it doubles the output tokens on the hosted side, and copying a long snippet is where a small model goes wrong. |
| D6. The coder follows it | One paragraph in `coder.md.j2`: with a `## Plan`, make its edits in order, treat its steps as scope, name every departure in the completion note | The rule applies only when the section exists, so the `improve` arm's coder prompt behaves as before. |
| D7. Arm per run | `route_lane`: even issue number → `plan`, odd → `improve`; fast lane → `none`. Context key `arm`, file `/tmp/fabro/arm` | The merge outcome is per PR, so every task of one run gets the same planner. Parity is deterministic, close to balanced in every repository, and a requeued issue keeps its arm. |

### Edges

`plan` takes improve's place on the three edges into it, each made exclusive on `arm` because
a condition tie breaks lexically and `improve` sorts first:

- `next_task -> plan` when `tasks_done=false && rebase_failed=false && lane!=fast && arm=plan`;
  `next_task -> improve` stays the unconditional fallthrough.
- `resolve_merge_gate -> plan` and `-> improve` split on `arm=plan` / `arm!=plan`.
- `improve_gate -> plan` and `-> improve` (the retry after an invalid result) split the same way.

`plan -> improve_gate` on success, `plan -> rescue_brief` otherwise. Backlog goes from 79 nodes
and 199 edges to 80 and 204. `ops/test-task-gates.sh` checks the arm choice and the edge
exclusivity.

## What is measured

`./ops/fabro-scorecard.sh --by-arm --from <deploy date>`, or `GET /api/scorecard/arms`, prints
both arms side by side. A run's arm is the planner node that ran (`trial.arm_of`). A fast-path
run, or one that ended before its first task, is in neither arm.

| Question | Metric | Source |
|---|---|---|
| Merge rate | PRs opened / merged by the workflow, non-architecture merged, held reasons, operator commits | the scorecard per arm |
| Code quality | rework visits per task (and hosted tiers t2-t4), `validate` failures per task, the Refuter's first verdict | `run_stage_stats`, `run_summary.refute` |
| Coder time | coder box-minutes per task, coder + rework minutes per task, time to first edit p50/p90, coder timeouts | `run_stage_stats`, `run_summary.first_edit_ms` |
| Planner cost | planner minutes per run p50/p90, planner box-minutes, planner tokens per run (input / cache read / output), planner timeouts, dispositions | `run_stage_stats` (tokens from each visit's `usage` in the stage list) |
| Throughput | box-hours per merged PR | lease time over merges, per arm |

Per-stage tokens and the first-edit times exist only for runs summarized after the scheduler
deploy. Use a window that starts after both deploys.

### Sizing, from the runs of 2026-10-04 to 10-13

- `improve` per run, summed over its visits (n=59 runs): fresh input p50 185k, p90 536k; cache
  read p50 197k, p90 1.04M; output plus reasoning p50 6.5k, p90 20k. If `plan` behaves the same,
  it adds about 0.6M hosted tokens to a run's mean, against about 4M hosted tokens per run today
  (1.07B over 266 runs).
- About 30 runs a day, so 50 per arm in 4 to 5 days.
- With about 50 PRs per arm, the merge rate can show only a gap of about 15 points or more. The
  per-task numbers (about 180 tasks per arm) carry the clearer signal.

## Risks to watch

- **z.ai concurrency.** `plan` is one more glm-5.3 stage per task, and `backlog` has no
  `glm-5.3` fallback row. Watch the scorecard's planner timeouts and failovers by node.
- **glm-5.3 stalls.** The known 15m39s hang before a 502 would cost one 20-minute attempt.
  The coder box sits idle under the run's lease while `plan` runs (ADR 0006), so box time saved
  shows up only as box-hours per merged PR.
- **The timeout is a peer value**, not measured on this node (graph comment above `plan`).
  Re-derive it from the trial's planner minutes.

## Ending the trial

Then pick one of these, write the result next to this file, and record the choice in an ADR:

1. `plan` wins: delete `improve`, `prompts/improve.md.j2`, its manifest stage and the `arm`
   edges and key. Keep `plan` and re-time it.
2. `improve` wins: delete `plan`, `.plan`, `prompts/plan.md.j2`, its manifest stage, the `arm`
   edges and key, and the coder prompt's plan paragraph. Then consider #25's own options.
3. Unclear: extend the window.

In every case, delete `trial.py`, `/api/scorecard/arms` and `--by-arm`. The token columns stay.
