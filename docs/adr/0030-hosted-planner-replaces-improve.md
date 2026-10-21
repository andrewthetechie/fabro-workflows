# A hosted planner replaces `improve` for every standard-lane task

**Status:** accepted (2026-10-21). It closes the trial of #25 (`docs/plan-trial/`) and
reverses ADR 0011's 2026-09-24 decision *not* to move `.improve` back to a hosted model.
The measurement is `docs/plan-trial/result-2026-10-21.txt`; it was taken before any of the
trial's code was deleted.

`backlog` planned each standard-lane task on one of two nodes, chosen per run by issue
parity in `route_lane`. Even issue numbers went to `plan`: `glm-5.3`, hosted, which did
`improve`'s job (check the task against the checkout, size it, split it, cite line ranges)
and also wrote the edit plan the coder follows. Odd numbers kept `improve`: the run's own
coder box, the same model as the coder, writing the dossier alone. Both wrote the same
contract (`improve_result.json`, schema `improve_result`, `improve_gate`, `excerpts`), so
the comparison was the planner and nothing else.

The window is 2026-10-14 to 2026-10-21: 80 `plan` runs over 182 tasks against 73 `improve`
runs over 145. The two arms did not draw the same repositories on the same boxes, and the
plan arm's PRs carried 2.60 tasks each against 2.13, so the numbers are read per
repo x coder-pool stratum and weighted by tasks. That is why the headline rows and the
stratified number agree.

| Metric | `plan` (hosted) | `improve` (box) |
|---|---|---|
| Coder box-minutes per task, cell-weighted | **10.86** | 12.32 |
| Rework visits per task (hosted tiers) | **0.14** (0.05) | 0.19 (0.06) |
| Coder + rework minutes per task | **12.3** | 14.0 |
| Non-architecture merged | **29 of 60 (48%)** | 25 of 59 (42%) |
| All PRs merged by the workflow | 30 of 70 (43%) | 28 of 68 (41%) |
| Refuter first verdict passed | **37 of 69** | 33 of 66 |
| Time to first edit, p50 / p90 min | 2.0 / **5.6** | 2.0 / 6.5 |
| Planner box-minutes | **0** | 1278 |
| Planner tokens per run (in / cached / out) | **0.13M / 1.33M** / 37k | 0.32M / 1.61M / **26k** |
| `validate` failures per task | 0.05 | **0.02** |
| PRs with operator commits | **10 of 70 (14%)** | 14 of 68 (21%) |
| Box-hours per merged PR | 6.2 | **5.5** |

Two readings favour `improve` on a headline row, and both are explained by the window's mix:

- **Box-hours per merged PR** (6.2 against 5.5) is task mix. Lease hours per task are 1.02
  under `plan` and 1.07 under `improve`; the plan arm's PRs simply carried more tasks each
  (6.07 tasks per merge against 5.18), and this row divides by merges.
- **`validate` failures per task** (0.05 against 0.02) is writers-app#1040: 7 of the plan
  arm's 10 failures, over 13 visits. The other 3 are two PRs.

The one per-run cost that is worse under `plan` is output tokens (37k against 26k), because
`improve` writes long prose dossiers where `plan` writes ordered steps; the plan arm's total
footprint is still the smaller one on both the input and the cache-read lines.

## D1. One planner, and it is the hosted one

`plan` takes `improve`'s place on every standard-lane route: `next_task`, `resolve_merge_gate`
after a resolved mainline merge, and `improve_gate`'s retry. `improve`, `.improve` and
`prompts/improve.md.j2` are deleted. `route_lane` writes only `lane`; the `arm` context key
and `/tmp/fabro/arm` are gone with the trial.

Rejected: keeping both nodes with parity routing. The trial's job was to choose; a permanent
parity split would keep two planners, two prompts and two model families in one graph to
measure forever, and the next trial (ADR 0029) needs one stable `backlog` as its control.

## D2. The contract keeps `improve`'s names

`improve_gate`, `/tmp/fabro/improve_result.json`, the `improve_result` schema and `excerpts`
keep their names although the node that writes them is now `plan`. Renaming them touches the
Stage manifest, the schema, the gate's receipt check, the split guards, the task budget and
every document that names them, for no change in behaviour. The gate's label changes to
"Validate planned task"; its comments say `plan` where they said `improve`.

## D3. `plan` is re-timed from its own stage times

`timeout="20m"`, `max_retries=1`, kept from the trial and now measured (n=182 successful
`plan` visits over 79 runs): per visit p50 4.7 min, p90 7.5, max 12.7; per run p50 7.3,
p90 27.7, max 63.2. 20m is 1.6x the slowest successful visit and 2.7x p90.

The house rule for the review nodes is 2.3x the slowest observed execution, which here would
be 29m; we do not take it. `plan` has one failure mode -- a provider stall that produces
nothing -- and it was observed once in 183 visits: the first attempt ran the full 20m, then
`max_retries=1` retried and the second attempt succeeded in 8.1 min. A stall therefore costs
the run about 28m of its lease, and every minute of the timeout is a minute of the box's
lease (ADR 0006) and of z.ai's three concurrent sessions (ADR 0003). 15m -- the review
peers -- would leave 2.3 minutes over the observed maximum, on one visit in 182 above 12
minutes. Too close.

One number in the window is not planner time and is excluded: run
`01M5B0G5ZVJ2X5X5KZZACGKFRK` records a 429-minute `plan` visit with no timeout and no
failure, because the 10-19 disk fill killed the worker and the operator's fabro restart
failed the phantom run the next morning. The `Planner timeouts` row, not `wall_ms`, is the
stall count.

## D4. The trial's measuring instrument is deleted

`ops/scheduler/src/fabro_scheduler/trial.py`, `GET /api/scorecard/arms`,
`ops/fabro-scorecard.sh --by-arm` and their tests are deleted with the trial. The per-stage
token columns and `first_edit_ms` stay: they were added for the trial and they answer
questions every later change asks. A future split by anything needs a new reader, not this
one.

## Consequences

- **The box now only codes.** `improve` was 24% of all stage time (#25) and, in this window,
  21.3 box-hours across 73 runs. Every task's dossier moves to the hosted provider, so the
  coder's lease is spent on coding and on waiting for a planner whose 3-session ceiling is
  shared with `decompose`, the reviews and the rework tiers.
- **z.ai concurrency is now load-bearing for every task.** `backlog` has no `glm-5.3`
  fallback row, so a z.ai outage stops the standard lane at `plan`; `improve_gate`'s retry
  and `rescue_brief` are the only recovery. Watch planner timeouts by node on the scorecard.
- **`plan` is worth about 1.5 coder box-minutes a task** at this window's numbers. That is
  small per task and large per day: 182 plan-arm tasks in 7 days, on two coder boxes.
- **Unchanged and unproven:** `plan` never ran a task that was already done, because issue
  parity never gave it an overlap. `improve`'s 3 `redundant` dispositions in the window are
  one run correctly reporting a duplicate issue. If a duplicate reaches `plan`, its
  disposition contract is `improve_gate`'s and unchanged, but no run has exercised it.
- **The next shape.** ADR 0029's `ralph` trial starts from this state: one `backlog` with
  `plan` and no `improve` (`docs/ralph/`, P1).
