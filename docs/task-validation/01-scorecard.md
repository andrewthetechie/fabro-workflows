# 01. Measure Task validation and Final validation in the scorecard

## Outcome
The scorecard has a **Task validation** row and a **Final validation** row (C9). Both can be
filtered to one repository with `--repo`. History is re-summarized, so the baseline window
for womens-fantasy-sports is readable on the day of the switch (C10).

## Why
D12 and D14. Today the scorecard counts `validate` as a share of idle time, not as a
per-visit duration, and nothing counts final-validation verdicts. Without this row, issue
#26's two targets cannot be checked. Summaries are built once per run, so the new fields
exist for old runs only if they are re-summarized.

## Change
1. `ops/scheduler/src/fabro_scheduler/summary.py`: collect `task_validations` and
   `final_routes` (C9). Copy the `DISPOSITION_GATE` pattern, which reads `context_updates`
   from `stage.completed`. Match the node id exactly: `validate` must not match
   `review_merge.validate`. Old runs and timeouts record `legacy` or `timeout`.
2. `store.py`: store the new fields. Follow how `dispositions` and `first_edit_ms` were
   added (a migration, with NULL meaning "not measured").
3. `scorecard.py`: compute the two rows, with p50 and p90 from `_percentiles`, and render
   them in `to_markdown` and `to_json`. Accept a `repo` filter, and thread it through the
   API route in `app.py`.
4. `ops/fabro-scorecard.sh`: add `--repo OWNER/REPO` (help text included).
5. Add a `resummarize --from D --to D` command (the C9 contract), reachable as
   `python -m fabro_scheduler resummarize …` (`app.main` dispatches on `argv`). It deletes
   only `run_summary` and `run_stage_stats` rows for runs dispatched in the window. It prints
   how many it deleted, and never touches `run_history`, leases or PR facts.
6. Tests in `ops/scheduler/tests/`:
   - a summary fixture with a scoped, a full, a timed-out and a legacy `validate` visit, and two `open_pr_prep` verdicts
   - a scorecard fixture checking p50 and the red final rate
   - the `--repo` filter
   - `resummarize` deleting only its window
7. `docs/scorecard/README.md`: the two new rows, `--repo` and `resummarize`.
8. Deploy: `make deploy-scheduler` from a clean checkout at `origin/main` (AGENTS.md:
   "Deploying"). Then, in the scheduler container, run `resummarize --from 2026-09-25 --to <today>`.
   Wait for the collector to finish the backfill: the Metrics tab's summarized count stops
   rising.

## Acceptance
- The scheduler's test suite passes, and `make check` passes.
- `ops/fabro-scorecard.sh --repo andrewthetechie/womens-fantasy-sports --from <today−7d>` prints
  both rows. Every `validate` visit reads `legacy`, and the final verdicts are not empty.
- The totals for the whole window (PRs, box-hours) equal what they were before the
  re-summarize.

## Depends on
Nothing. Do this first: the trial's baseline depends on it.
