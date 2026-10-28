# The box-plan trial

**Status:** running since 2026-10-28. It re-opens one question that ADR 0030 closed on
2026-10-21: does the planner belong on a hosted model or on the run's own coder box?

## Why now

- **The box changed.** Both coder boxes (`coders-a`, `coders-b`) now serve
  qwen3.8-flash-next (QFN). Fabro's per-stage usage shows box-a at 33-43 output tokens
  per wall-second from about 10-22 and box-b at 33-45 from 10-24, against about 10 on
  the DeepSeek build that ran `improve` in #25. glm-5.3 runs `plan` at about 51.
- **The hosted planner is the largest z.ai cost.** z.ai bills the GLM Coding Plan in
  credits per token (docs.z.ai/devpack/overview). In the week the quota ran out
  (2026-10-22..27), backlog's `plan` on glm-5.3 was 22% of all credits, and ralph's
  `plan` added 6.7% through its kimi fallback.

## The arms

One rule in both stylesheets (`backlog/workflow.fabro`, `ralph/workflow.fabro`):

| `(issue_number // 2) % 2` | `.plan` runs on |
|---|---|
| 1 | the run's leased box, `{{ inputs.coder_pool }}`, `reasoning_effort: medium` |
| 0 | backlog: `glm-5.3`. ralph: `kimi-k3`, then its fallback chain |

The rule is not parity, because `SCHEDULER_RALPH_SHARE=50` already sends even issues to
ralph. With that split, backlog's odd issues alternate 1, 3 → hosted, box; 5, 7 →
hosted, box. Ralph's even issues alternate the same way. An unbound `issue_number`, as
in a hand fire without it, reads 0 and plans hosted.
`fabro preflight <workflow.toml> -I issue_number=N -I coder_pool=coders-b -v` names the
arm's model in its `LLM` line. That check was run on 2026-10-28 for 5, 6, 7, 8, 9, 11
and unbound.

Nothing else differs between the arms. The prompt, the contract (`improve_result.json`),
`improve_gate` and every later stage are the same.

## Timeouts

A node has one timeout, so it is sized for its slower arm and re-derived from per-visit
fabro data for 2026-10-21..28:

- backlog `plan`: 20m → **30m**. glm-5.3 had p95 11.6m and a heaviest visit of 57k
  output tokens, which projects to about 26m on QFN.
- ralph `plan`: 20m → **40m**. The heaviest visit was 73k tokens: about 34m on QFN, and
  about 40m on the flash rung that now carries the hosted arm while Kimi's quota is
  spent.

Re-time both from the trial's first ten QFN visits.

## What to measure

The arm is the `plan` stage's model in `GET /runs/{id}/usage`: a `box-a` or `box-b`
provider is the box arm. Stratify by pool. `coders-d` is freellm, not QFN, so read its
runs separately or drop them. It was drained when the trial started.

Per task or per run, for each arm, the same rows as ADR 0030's table:

- non-architecture merges, and all PRs the workflow merged
- coder and rework minutes per task, and rework visits per task (hosted tiers)
- Refuter first verdict passed
- `validate` failures per task, and PRs with operator commits
- `plan` minutes per visit and `plan` timeouts
- box-hours per merged PR, the cost side for the box arm
- z.ai credits per run, the benefit side, using the rates in the docs above and the
  off-peak halving

## Decision

Run about two weeks, or until each backlog arm has about 80 runs (#25 needed about 80).
The box arm wins if its merge rate is no worse and its coder and rework minutes per task
are no worse by more than their week-to-week noise. If it wins, the box planner's credit
saving decides it, and an ADR records the reversal of ADR 0030 D1. If it loses, delete
the `{% if %}` block in both stylesheets and keep the 30m and 40m only if the hosted
visits need them.

## Confounds

- The ralph trial (ADR 0029) is running at the same time. Read the two workflows'
  arms separately.
- On 2026-10-28 three changes landed with this trial: per-task `review` and `quality`
  moved to glm-5.3-flash, ralph's `kimi-k3` fallback starts at glm-5.3-flash, and the
  scheduler stopped dispatching in z.ai's peak hours. All three apply to both arms
  alike.
