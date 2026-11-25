# Factory scorecard

**Status:** deployed 2026-10-12 06:11Z (`eb369f2`) for issue #22 (roadmap B1, reduced). The
production collector backfilled all of `run_history` within about 90 minutes, and its retro
window matches the staging run below line for line.

The 2026-10-11 retro was assembled by hand from four sources, and exporting raw events
for it filled the Mac's disk. The scorecard computes the same numbers continuously, on the
host, and shows them in two places:

- the coder scheduler's **Metrics** tab (`http://10.10.0.32:32280/metrics`): stat tiles,
  week-by-week trends, and one window in detail;
- `ops/fabro-scorecard.sh`, which prints the weekly table for a window
  (`--from/--to`, `--week`, `--last-week`, `--json`, `--by-workflow`).

Both read `GET /api/scorecard`, which is one computation (`scorecard.compute`), so they can
never disagree.

## The trial's two arms (ADR 0029 C12)

`GET /api/scorecard/workflows`, and `ops/fabro-scorecard.sh --by-workflow`, print the same
window split by `run_history.workflow`: one ordinary card per arm (so every number below is
defined once), a **repo × coder pool strata table** per arm — runs, PRs, merged, box-hours,
box-hours per merge — and a `ralph` line with the numbers only that arm has:

| Field | What it is |
|---|---|
| Iterations per run (p50/p90) | visits to `iterate_t1`..`iterate_t4`. One Task may take several; the cap is `min(24, 3 × the Task list)` before the PR (O7). |
| Refute visits per run (p50/p90) | visits to `refute`. O8 allows at most 3 rounds. |
| Tasks by source | every Task in the run's `tasks.json`, counted by `source` (`plan`, `refute`, `review`, `final_fix`, `ci`). A run whose Tasks are mostly `refute` ones is a run whose plan missed. The collector reads the file once at summary time. |
| runs that ran out | summaries whose loop published `loop_exhausted` (O18). |
| post-PR Iterations | Iterations that ran after `pr_handoff`. O13 allows 2. |
| queued hand-offs | runs whose ending was `report_queued`: the PR passed every gate and went to merge-on-green (O12). |
| Refuter tokens (in / cached / out) | the `refute` rows' tokens, so task 12 can price them at luna's rates. The catalog has no cost rows for OpenAI or z.ai, so the money columns read $0 for both. |

A window with no `ralph` run still gets the arm's column, at zero: a table that loses a
column when the arm is idle is a table nobody can read. `--by-workflow` has no `--repo`,
because its strata table already has every repo in it.

The arm's two numbers that O3 guards are the split's reason to exist: PRs merged with no
operator commit, and box-hours per merged PR, each per repo × pool. A merge rate that rose
because `ralph` ran on the faster box would be an artefact, and the strata table is what
shows it.

## The ralph-v2 trial (ADR 0034, ralph-v2 11)

The ralph-v2 trial compares `ralph` with `ralph-v2` the same way, so the split gained two
sections: `## ralph-v2 trial`, which is the experiment's own measures, and `## Task reviews`,
which is the loop only v2 has. `ops/fabro-trial-review.sh --arms ralph,ralph-v2 --from
<switch date>` reads the same window. The experiment, its stop rules and its result rule
are in `docs/plans/ralph-v2/00-overview-and-contracts.md`.

**The first-round rule** is the one thing to hold on to: the quality measure reads what the
three hosted reviewers **submitted** on their **first completed visit**, never what their gate
later decided and never a later round. So it counts the Refuter's `not_met` criteria, its
defects and its `tampering` rulings, plus `standards`' and `spec`'s error findings, from the
last `submit` before that visit's `stage.completed` -- an `arguments` that is not an object is
not a submitted result. A stage that never completed leaves its own fields NULL, so a run that
stopped before the Refuter is not a run that found nothing, and it is left out of the mean
rather than counted as 0. A re-summarize picks the round up: the counts are stored in
`run_summary.first_round`, and a row summarized before the column existed reads NULL.

| Field | What it is |
|---|---|
| runs | the arm's runs in the window, including the ones that opened no PR (the experiment counts every run) |
| zero-touch merges | runs whose PR the workflow merged with no operator commit: `merged_by_workflow` and the PR's `operator_commits` 0. A PR whose facts GitHub has not answered for is not credited yet |
| merge rate (standard error) | zero-touch merges over runs, and its binomial standard error `sqrt(p(1-p)/n)` |
| quality, sum of the five: n / mean / sd | over the arms' runs whose five counts were all measured: the five summed, then n, the mean and the **sample** standard deviation (None below 2 runs) |
| quality, the Refuter's three | the same over the Refuter's three counts alone, for the reviewer one arm can be read on its own |
| runs that ran out | summaries whose loop published `loop_exhausted` (the `ralph` line's number, per arm) |
| lease-hours / per zero-touch merge | lease time dispatch to release, and that over the zero-touch merges: the guardrail |
| merge, v2 - v1 | the difference of the merge rates, the standard error of the difference `sqrt(p1(1-p1)/n1 + p2(1-p2)/n2)`, and `better`, `worse` or `unclear` at 2 standard errors. More merges is better |
| quality, v2 - v1 | the same for the five-count mean, `sqrt(s1²/n1 + s2²/n2)`. Fewer findings is better |
| guardrail, v2 / v1 | v2's lease-hours per zero-touch merge over v1's. The trial is lost above +50% |
| stop rules | the three weekly checks: `lease` above 2x after 40 v2 runs, `merge` 15 points below after 60, and `misses` above 10% of the window's Task reviews |

The differences and the guardrail are NULL below 2 runs in either arm -- one run is not a
comparison -- and each stop rule stays `no` until its own count is reached. The `Task reviews`
section reads `run_task_reviews`, one row per round of `ralph-v2`'s Task loop (E7-E13):

| Field | What it is |
|---|---|
| reviews (rounds) | rows, so a Task reworked twice is three. A round is **two** reviewers |
| verdicts | `pass`, `missed` (both reviewers left no valid result), `rework` (an error, rounds 1-3) and `blocked` (an error on the fourth) |
| rejection rate by round | the share of each round's reviews that rejected the work (`rework` or `blocked`), by round number |
| Tasks blocked | rows whose verdict is `blocked`: the Task a box review failed (E10) |
| disputes / withdrawals | findings the coder disputed, and how many of them the next reviewer withdrew (E12) |
| served, Refuter / review | per reviewer: `box`, `fallback` (the third box try came back empty, so it ran on `glm-5.3-flash`, E13) or `missed`. A reviewer that **rejected** the work is neither, and is never a miss (E11) |
| misses | reviewer visits with no valid result, one per reviewer |
| reviewer minutes p50 / p90 | the four reviewer stages' wall time a round, from the events |
| edge cases planned / covered | what the accepted Task plans wrote, against what the **first** review of each Task found covered: later rounds restate the same edge cases |
| box minutes a run | what `task_plan`, `task_refute`, `task_review` and `iterate_t1` spent on the box, per v2 run (`run_stage_stats.box_ms`) |
| plan types | the plans `task_plan_gate` accepted, by type (`planned` or `fallback`); a rejected visit routes `retry` and wrote no plan |

The third stop rule's rate is also `GET /api/box-health`'s `miss_rate`, which
`fabro-monitor.sh`'s C11 alerts on: two reviewers a round, so a row's `missed` services are its
misses. A window with no `ralph-v2` run reads 0 reviews and no rate, which is what C11's
`>= 20` reviews guard is for.

## The sharpening row (ADR 0033, issue-sharpening 06)

The factory rewrites an `unverifiable` issue's acceptance criteria itself, with no human approval,
so the scorecard carries one row for it:

| Metric | Value |
|---|---|
| Sharpened | applied A, released unapplied U; applied issues' PRs merged M of P |

`A` is the sharpenings that ended `applied` in the window, and `U` those released without an apply
(`capped`, `churn`, `cannot_sharpen`, `failed`, `stale`): the issue then ran as written. `P` counts
the PRs opened by issue runs dispatched **after** an applied sharpening ended, for that same issue,
and `M` how many of them the workflow merged (the first card's definition of merged). The row is
the short form. `GET :32280/api/background/measure` has the `sharpening` section: rows by state,
rounds, transformations, `recheck` verdicts, box minutes, hold-to-release times, each stage's
duration, and that merge rate beside the calibration report's `unverifiable` and `ready` rates and
the do-nothing rate of the unapplied rows. The retro files `sharpen:<state>` classes from the same
rows when one crosses its thresholds.

## How the numbers are collected

```
fabro run store ──stages + streamed events──▶ ScorecardCollector ──▶ run_summary
                                              (in the scheduler)      run_stage_stats
GitHub ──pull, commits, comments─────────────▶                        run_failovers
                                                                      pr_facts
run_history (written at release) ──────────────────────────────────▶ scorecard.compute
```

- **`collector.py`** is a background thread in the scheduler, a read-only observer like
  the run probe. Every 30 s (`SCHEDULER_SCORECARD_SECONDS`) it summarizes up to two
  released runs that have no summary yet, newest first, and reads up to ten PRs.
- **`summary.py`** reduces a run. Stage time, visits and the provider each visit used come
  from `GET /runs/{id}/stages`. Timeouts, failure categories, provider failovers, the edge
  path into the run's end and the refuter's verdicts exist only in the event log, so
  `GET /runs/{id}/events` is streamed 1,000 events at a time and reduced on the fly. A run's
  events are 5 to 25 MB; what is stored is a few hundred bytes. The insights SQL endpoint
  (`POST /api/v1/insights/execute`) would avoid the streaming but answers `501` on 0.362.
- **`prfacts.py`** reads each factory PR until it is merged or closed: its state and
  labels, its commits' authors, and the merge phase's last report comment.
- Everything is derived data in four SQLite tables beside `run_history`. Dropping them
  loses nothing; the collector rebuilds them from fabro and GitHub. A run fabro no longer
  has (404) is recorded `missing` and not retried.

## What each metric counts

A **window** holds the runs dispatched in it. Each PR belongs to the window of the first run
that opened it, and shows what GitHub says about it now, so a recent week's merge rate rises
as its PRs merge.

The runs are the scheduler's `run_history`: every `backlog` run it dispatched. A hand fire
(`fabro-fire-backlog.sh`), a `pr-review` run on a human's PR, `dependabot` and `arch-review`
are not in it.

| Metric | Definition |
|---|---|
| PRs opened / merged by the workflow | Distinct PRs the window's runs opened. **Merged by the workflow**: a release row says the merge phase merged it, or it merged after the merge phase queued it (`<!-- fabro:merge-queue approved -->`) and the merge queue did not hand it off. `mergedBy` cannot separate the workflow from the operator: both use one account. |
| Non-architecture / architecture merged | The same, split on the PR's `architecture` label. Architecture PRs auto-merge since 2026-10-12 (ADR 0012 D8 amendment). |
| Held PRs, by reason | Every PR not merged by the workflow, bucketed from the `**Reason:**` line of its last merge-phase report (`scorecard.REASON_BUCKETS`). `no report` is a PR whose run never reached a report; `queued` is waiting in the merge queue. |
| PRs with operator commits | PRs with at least one commit whose author is not `SCHEDULER_AUTOMATION_AUTHOR` (default `andrews-ai-agent`, matched on the login or the noreply address). |
| Box-hours per merged PR | Lease time (dispatch to release) of every run in the window, over workflow merges. One lease is one coder box. |
| Lease share | Of the leased time of summarized runs: time a stage spent on the run's coder pool (`provider_used.model` is `coders-*`), split into `improve` and writing code; and the box's idle time, by phase: hosted agents, `validate`, the merge phase (`review_merge.*`), human gates, other command stages, and overhead (lease time no stage accounts for: dispatch, sandbox start, the gap before release). |
| Timeouts by node | `stage.failed` events with `system_actor: "timeout"`. Every timed-out attempt counts, retried ones included. A timed-out stage can still show `succeeded` in the stage list. |
| Provider failovers | `agent.route.failover` events, by node, route and the provider error's `llm_kind`; the count is runs, not events. |
| Runs with no PR, by terminal node | Runs whose release found no PR, by the last node they started. |
| Refuter verdicts | Each run's `refute_gate` verdicts in order: the first one split pass / fail with a `not_met` / other fail / invalid, then fix turns taken (a second verdict after a first `not_met`) and how many passed. A fail recorded before gate relief has no `not_met` flag. This is gate-relief task 07's measurement. |
| Refuter hygiene rulings | The refuter's rulings on diff-hygiene hits (#92), from the three counts `refute_gate` publishes with every verdict: hits whose final ruling was `legitimate` (`hygiene_cleared`), `unsure` (`hygiene_unsure`) and `tampering` (`hygiene_tampering`). Each is summed over **every** verdict of a run, because in `ralph` a later round rules again. The row also says over how many runs: a run counts only when its gate published the counts, which no run from before the rulings does, so such a window reads `0 / 0 / 0 over 0 runs`. An absent count is never read as 0. |
| Ruled merges | PRs the workflow merged (merged as the first row defines it) that one of their runs cleared a hygiene hit for. Under the weekly table, *Ruled merges* lists each ruling run with its repository, a link to the PR, the run id and the hits it cleared, so every merge that relied on a ruling can be opened and read; one confirmed bad clear reverts the rulings. A PR two runs ruled on is one ruled merge and two lines. The Metrics tab has the same counts in *The refuter* and the same list under *Ruled merges*. |
| Open to merge | Hours from the PR opening to its merge, by anyone; p50 and p90, architecture and other. |
| Tokens by model | Every model call in the window's runs, from fabro's `GET /runs/{id}/usage` `by_model` rollup, which the collector reads once per summarized run into `run_model_usage` (`usage.model_rows`; speed tiers of one model are summed). Per provider and model: runs, stage visits, input, output (with reasoning), cache read plus write, total, share of the window's tokens, and fabro's priced cost. A stage that failed over counts under the model that served it, a subagent under its own model. Local is a LAN coder box (`usage.LOCAL_PROVIDERS`), which fabro does not price. `run_summary.model_usage` records whether a run has been read (`ok`, `none`, `missing`, `error`, retried after an hour), so the first tick after a deploy backfills all of history, 20 runs a tick. |
| Task validation | Every `backlog` `validate` visit (ADR 0025, `docs/task-validation/`), one entry per visit in `run_summary.task_validations`. **Mode** is `scoped` (the checks and the tests that cover the diff), `full` (the repository's `setup.sh && ci.sh`, on a fallback), `legacy` (a visit summarized before the change, or one with no routing keys) or `timeout`. The row gives the visits by mode with per-visit minutes p50 and p90 (and all visits together), the `full` visits by fallback reason class (`no_config`, `full_glob`, `deleted`, `index_unavailable`, `no_base`, `unowned_test`, `path_outside_cwd`, `too_many_tests`, `final_fix`, `task_check_error`), the red visits per mode, the unchecked changed files (`task_validation_unchecked`, summed: files no check watches and no target covers; a check that watches `**` makes it 0), and the untested changed files (`task_validation_untested`, summed: changed files that selected no test, the gap Final validation has to catch). A red legacy visit is recorded from its `stage.failed`, which is the only event it emits. |
| Final validation | Runs that recorded a verdict at `open_pr_prep` (row label `verdicts / first route`), and the first `final_route` of each, split `pr` / `fix` / `drop` / `rescue` / `draft` (`run_summary.final_routes`; `open_pr_prep` can be visited twice after a `final_fix`, and only the first verdict counts). **Red final rate** is the runs whose first `final_route` is not `pr`, over the runs that recorded one. A run that visited `open_pr_prep` with no route recorded is shown as "N more with no route recorded" and never counted as red: `open_pr_prep` published no `final_route` before #35 (2026-10-13 22:13Z), so a window before then has no Final validation verdicts at all. |

## The plan trial (#25), closed

The trial's splitter is gone with it (ADR 0030): `GET /api/scorecard/arms`,
`ops/fabro-scorecard.sh --by-arm` and `trial.py` were deleted on 2026-10-21, and the
result is `docs/plan-trial/result-2026-10-21.txt`. `backlog` now plans every standard-lane
task on the hosted `plan` node, so there is nothing to split by. The three facts the trial
added stay in every summary, and are read by the ordinary scorecard:
`run_stage_stats.input_tokens/output_tokens/cache_read_tokens` (summed from each visit's
`usage` in the stage list; output includes reasoning), `run_summary.first_edit_ms` (per
`coder` attempt, `stage.started` to its first `edit_file`/`write_file`) and
`run_summary.dispositions` (`improve_gate`'s `task_disposition`). Runs summarized before the
scheduler deploy have NULLs there, which reads as "not measured", never as zero.

## Re-summarizing a window (ADR 0025, C9)

The node ids a summary reads are per workflow (`summary.NODES`, ADR 0029 C12): `backlog`
summarizes `review_merge.refute_gate`, `coder`, `validate` and `open_pr_prep`; `ralph`
summarizes `refute_gate`, `iterate_t1`..`iterate_t4`, `check` and `final_check`. A `ralph`
run's ending is the node that routed into `learn_prep`, taken from the run's last edge,
because every terminal node there goes through the learnings stage and its last started
node is always `learn_post`.

A summary is built once, so a field added to `summary.py` exists for an old run only
after it is re-summarized. `python -m fabro_scheduler resummarize --from D --to D`, run
inside the scheduler container, deletes the window's `run_summary`, `run_stage_stats` and
`run_task_reviews` rows and prints how many; the collector's next ticks stream the events
again. The review rows go with the summary because the same events write both. Both dates
are inclusive, and `run_history`, the leases and the PR facts are never touched. The
first `resummarize` after the Task validation deploy (#26) rebuilt the whole history so
the trial had a baseline.

A stage's wall time changed source in #66 (2026-11-04): it is now the sum of every
attempt's `timing.wall_time_ms` from the events, not the stage list's `wall_time_ms`,
which recorded only the final attempt of a retried stage (so every timed-out stage read
low). The re-summarize for #66 covered everything dispatched since 2026-10-19; any run
dispatched before then still reads its pre-#66 (single-attempt) stage time until it is
re-summarized.

`ops/fabro-scorecard.sh --repo OWNER/REPO` (or `?repo=` on `GET /api/scorecard`) narrows
every series to one repository, which is how the womens-fantasy-sports trial window is
read.

## Verification

Checked on 2026-10-12 before deploy. A throwaway container on the host ran the new collector
over a copy of the production scheduler database (297 released runs; 50 runs dispatched
before 2026-09-25T17:15 are gone from fabro and were recorded `missing`), then computed the
retro's window, 2026-09-25 to 2026-10-11T03:00 (204 runs):

| Metric | Retro (by hand) | Scorecard |
|---|---|---|
| PRs opened / merged by the workflow | 176 / 26 (15%) | 176 / 26 (15%) |
| Non-architecture merged by the workflow | 24 of 80 (30%) | 24 of 80 (30%) |
| Held PRs, by reason | architecture 90, refuter 18, authorship 16, hygiene 6, risk 5, unspecified 5, unfixed findings 3, CI-fix 3, force-with-lease 2 | architecture 91, refuter 17, authorship 16, hygiene 7, risk 5, unspecified 5, unfixed findings 3, CI 4, branch moved 2 |
| PRs with operator commits before merge | 50 of 183 (27%), 77 commits | 64 of 176 (36%), 99 commits |
| Box-hours | 735 | 735 |
| Lease share: writing code / improve / idle | 32% / 24% / 44% | 32% / 22% / 47% |
| Idle: hosted reviews / validate / merge phase / human_rescue | 12% / 11% / 11% / 4% | 15% / 10% / 10% / 4% |
| Timeouts by node | improve 25, review_fix 11, rework_t1 5, standards 3, decompose 2 | the same, plus coder 1 and review 1 |
| Provider failovers | `spec` and `extra_decompose`, Kimi weekly quota, 78 runs | `spec` 78 runs, `extra_decompose` 77, `rework_t3` 5, all `access_denied` |
| Runs with no PR | 28 runs, 81 box-hours | 28 runs, 81 box-hours |
| Architecture PR open to merge | p50 5.2 h, p90 34 h | p50 5.2 h, p90 32 h |

Two differences are explained:

- **Operator commits.** The retro read commits with `gh pr view --json commits`, which
  returns at most 100, and a factory PR carries one checkpoint commit per stage. 13 of the 64
  PRs have more than 100 commits; without them the count is 51, the retro's 50. The scorecard
  pages through every commit. It also counts the automation's pre-identity commits
  (`Fabro <noreply@fabro.sh>`) as the automation's, which the first staging pass did not
  (361 "operator" commits until that was fixed).
- **Hosted agents** counts every agent stage off the coder pool outside the merge phase
  (`spec`, `extra_decompose`, task reviews), not only the task reviews.

Collection cost: about 5 s per run summarized (in production, two runs per 30 s tick), 90 MB of memory, and 3 GitHub requests per PR.

## Not built

- B1's revert detection (a factory PR reverted or re-fixed within 14 days), proposal
  acceptance, cost by model, and the seeded-defect suite.
- The weekly retro (`docs/factory-retro/`, ADR 0026) covers this line: it posts a
  scorecard summary to Discord every Monday and files issues for the week's failure
  classes. `ops/fabro-scorecard.sh --last-week` still prints the table to paste by hand.
