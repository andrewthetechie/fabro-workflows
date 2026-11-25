# Task 11: measurement

Read `00-overview-and-contracts.md` first (*The experiment*, C4, C5 step 9). Read `summary.py` whole,
`store.py` (`save_run_summary`, the added-columns table, `scorecard_rows`), `scorecard.py` (`by_workflow`,
`_ralph_line`), `measure.py`, and `baseline-queries/extract-events.py` for where submitted results sit in
events. **Never export raw events**: the collector streams.

The experiment cannot start before this is deployed: its numbers come from here.

## Steps

1. **`summary.py`**, for `ralph` and `ralph-v2`:
   - `first_round: FirstRound | None`: for `refute`, `standards` and `spec`, the **last**
     `agent.tool.started` whose tool name ends in `submit` before that node's first `stage.completed`.
     From `properties.event.ToolCallStarted.arguments`: `refute_not_met` (criteria with status `not_met`),
     `refute_defects` (length of `defects`), `refute_tampering` (entries of `hygiene` with ruling
     `tampering`), `standards_errors`, `spec_errors` (findings with severity `error`). A stage that never
     completed leaves its fields None. A non-dict argument counts as not submitted.
   - `task_reviews: list[TaskReview]` (v2 only): one per `task_review_gate` `stage.completed` with
     `context_updates`: the thirteen keys of C5 step 9, `ms` (summed wall time of `task_refute`,
     `task_refute_fb`, `task_review`, `task_review_fb` visits since the previous gate), `tries` (the sum
     of the last `task_refute_tries` and `task_review_tries` since then) and `failover` (an
     `agent.route.failover` on one of the four since then).
   - `task_plans: Counter` of `task_plan_type` over `task_plan_gate` visits whose route is `go`, and the
     sums of `task_plan_goals` and `task_plan_edges` over them.
2. **`store.py`**: `run_summary` gains `first_round TEXT` (JSON) and `task_plans TEXT` (JSON), both in the
   CREATE and the added-columns table. New table `run_task_reviews`, primary key `(run_id, seq)`:
   `run_id, seq, task, source, round, verdict, errors, warnings, disputed, withdrawn, dropped,
   refute_served, review_served, edges, edges_covered, tries, ms, failover`. Saved and replaced in
   `save_run_summary`'s transaction, and deleted with the other per-run rows where they are pruned. A run
   summarized before the columns exist reads NULL: "not measured", never 0.
3. **`scorecard.py`**, `by_workflow`:
   - `ArmLine` per arm in `("ralph", "ralph-v2")`: runs; zero-touch merges (`merged_by_workflow` and the PR's
     `operator_commits` is 0); merge rate and its standard error; quality n, mean and sample standard
     deviation over runs whose `first_round` has all five fields (the sum of the five); the same for the
     Refuter's three fields alone; exhausted runs; lease-hours; lease-hours per zero-touch merge.
   - `TrialLine`: the differences v2 - v1 of merge and quality, each with its standard error and
     `better | worse | unclear` (above 2 standard errors, the sign per measure: more merges is better, fewer
     findings is better), the guardrail ratio, and the stop rules' states: `lease_stop` (ratio above 2 with 40 or
     more v2 runs), `merge_stop` (15 points below with 60 or more), `miss_stop` (misses above 10% of reviews
     in the window). Fields are null below 2 runs in either arm.
   - `TaskReviewLine`: reviews; verdicts; rejection rate by round; Tasks blocked; disputes, withdrawals;
     served counts; misses; `ms` p50/p90; edge cases planned and covered; box minutes of `task_plan`,
     `task_refute`, `task_review`, `iterate_t1` per run; plan types.
   - Both in `workflows_to_json` (`trial`, `arms`, `task_reviews`) and in `workflows_to_markdown` after the
     strata table, headed `## ralph-v2 trial` and `## Task reviews`. Nothing existing changes shape.
4. **`measure.py`**: `box_health` (task 02) fills `reviews`, `reviews_missed` and `miss_rate` from
   `run_task_reviews` of the window's runs (a review is one reviewer: two per row; missed is
   `refute_served` or `review_served` equal to `missed`). C11 needs no edit.
5. **`ops/fabro-trial-review.sh`**: `--arms A,B` (default `backlog,ralph`), passed to
   `fabro-trial-review.py`, whose hard-coded pair becomes the two names; the ralph-only sections run for each
   name that starts with `ralph`. `--arms ralph,ralph-v2` prints `/api/scorecard/workflows?format=md` first, as
   today. The default output is unchanged (test on the fixture it has, or add one).
6. **`docs/scorecard/README.md`**: the new lines, what each counts, and the first-round rule.

## Tests

Fixture event streams: a clean v2 run; a reworked Task; a blocked Task; one reviewer missed; a fallback; a
mid-session failover; a run that stopped before any review; a v1 run; a v1 run from before the columns. For
each: the summary, the stored rows, the arm lines. `TrialLine` on hand-computed numbers (write the arithmetic
in the test). A re-summarized run replaces its rows. The v1 column of `by_workflow` over the existing fixture
equals today's output byte for byte.

## Done when

- `uv run pytest` in `ops/scheduler` and `make check` pass.
- Commit: `feat(scorecard): the ralph-v2 trial's measures, per-Task reviews and stop rules (ralph-v2 11)`.
