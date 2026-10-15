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
  (`--from/--to`, `--week`, `--last-week`, `--json`).

Both read `GET /api/scorecard`, which is one computation (`scorecard.compute`), so they can
never disagree.

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
| Open to merge | Hours from the PR opening to its merge, by anyone; p50 and p90, architecture and other. |
| Tokens by model | Every model call in the window's runs, from fabro's `GET /runs/{id}/usage` `by_model` rollup, which the collector reads once per summarized run into `run_model_usage` (`usage.model_rows`; speed tiers of one model are summed). Per provider and model: runs, stage visits, input, output (with reasoning), cache read plus write, total, share of the window's tokens, and fabro's priced cost. A stage that failed over counts under the model that served it, a subagent under its own model. Local is a LAN coder box (`usage.LOCAL_PROVIDERS`), which fabro does not price. `run_summary.model_usage` records whether a run has been read (`ok`, `none`, `missing`, `error`, retried after an hour), so the first tick after a deploy backfills all of history, 20 runs a tick. |
| Task validation | Every `backlog` `validate` visit (ADR 0025, `docs/task-validation/`), one entry per visit in `run_summary.task_validations`. **Mode** is `scoped` (the checks and the tests that cover the diff), `full` (the repository's `setup.sh && ci.sh`, on a fallback), `legacy` (a visit summarized before the change, or one with no routing keys) or `timeout`. The row gives the visits by mode with per-visit minutes p50 and p90 (and all visits together), the `full` visits by fallback reason class (`no_config`, `full_glob`, `deleted`, `index_unavailable`, `no_base`, `unowned_test`, `path_outside_cwd`, `too_many_tests`, `final_fix`, `task_check_error`), the red visits per mode, the unchecked changed files (`task_validation_unchecked`, summed: files no check watches and no target covers; a check that watches `**` makes it 0), and the untested changed files (`task_validation_untested`, summed: changed files that selected no test, the gap Final validation has to catch). A red legacy visit is recorded from its `stage.failed`, which is the only event it emits. |
| Final validation | Runs that recorded a verdict at `open_pr_prep` (row label `verdicts / first route`), and the first `final_route` of each, split `pr` / `fix` / `drop` / `rescue` / `draft` (`run_summary.final_routes`; `open_pr_prep` can be visited twice after a `final_fix`, and only the first verdict counts). **Red final rate** is the runs whose first `final_route` is not `pr`, over the runs that recorded one. A run that visited `open_pr_prep` with no route recorded is shown as "N more with no route recorded" and never counted as red: `open_pr_prep` published no `final_route` before #35 (2026-10-13 22:13Z), so a window before then has no Final validation verdicts at all. |

## The plan trial (#25)

`GET /api/scorecard/arms` and `ops/fabro-scorecard.sh --by-arm` split one window by the
planner node each run took, `plan` or `improve` (`trial.py`, `docs/plan-trial/`). Each arm
gets the whole scorecard plus a per-task line: planner minutes and tokens, coder box-minutes
per task, rework and `validate` failures per task, coder timeouts, the coder's time to first
edit, and the planner's dispositions. The trial added three facts to every summary:
`run_stage_stats.input_tokens/output_tokens/cache_read_tokens` (summed from each visit's
`usage` in the stage list; output includes reasoning), `run_summary.first_edit_ms` (per
`coder` attempt, `stage.started` to its first `edit_file`/`write_file`) and
`run_summary.dispositions` (`improve_gate`'s `task_disposition`). Runs summarized before the
scheduler deploy have NULLs there, which the split reads as "not measured".

## Re-summarizing a window (ADR 0025, C9)

A summary is built once, so a field added to `summary.py` exists for an old run only
after it is re-summarized. `python -m fabro_scheduler resummarize --from D --to D`, run
inside the scheduler container, deletes the window's `run_summary` and `run_stage_stats`
rows and prints how many; the collector's next ticks stream the events again. Both dates
are inclusive, and `run_history`, the leases and the PR facts are never touched. The
first `resummarize` after the Task validation deploy (#26) rebuilt the whole history so
the trial had a baseline.

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
- A weekly Discord line. `ops/fabro-scorecard.sh --last-week` prints the table to paste.
