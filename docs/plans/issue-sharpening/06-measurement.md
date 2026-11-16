# Task 06: measurement: the measure section, the scorecard row, the retro classes

Read `00-overview-and-contracts.md` first (D6, D21; C11). Read `measure.py`, `scorecard.py`
(`held_bucket`, the endings, the Markdown table), `retro.py` (`classify`, the class kinds), and
`docs/scorecard/README.md`.

## Goal

A dark loop has no human watching each proposal, so the numbers must say whether it raises
merges, what it costs, and when it goes wrong.

## Steps

1. **`GET /api/background/measure`** gains a `sharpening` section (JSON and `&format=md`):
   - issues held now as `precheck-unverifiable`;
   - rows by terminal state in the window;
   - rounds per row (1/2/3);
   - transformations used (counts);
   - `recheck` verdicts after `applied`;
   - box minutes per row (p50 and p90) and in total;
   - time from hold to release (p50 and p90);
   - **the merge rate of applied issues**: issue runs dispatched after the row's `finished_at`
     for that issue, merged by the workflow, against the `unverifiable` and `ready` rates from
     the calibration report over the same window. Also the rate for issues released without an
     apply (`capped`, `churn`, `cannot_sharpen`, `failed`), the do-nothing comparison.
   - each stage's duration p50/p90/max, from the runs' stage events, so task 08 can re-derive
     D21's timeouts.
2. **The scorecard** gains one row:
   `Sharpened | applied A, released unapplied U; applied issues' PRs merged M of P`. Describe it in
   `docs/scorecard/README.md`.
3. **The retro** gains a class kind `sharpen:<state>`, one per terminal state except `applied`
   and `not_needed`. It counts rows and box-minutes, so `capped`, `churn`, `cannot_sharpen`,
   `failed` and `stale` file themselves when they cross the thresholds. Each issue body names
   `GET :32280/api/background/measure` and the run ids.
4. **Tests** for each number, on store fixtures, including the merge-rate join and its window
   edges, and a retro fixture that files `sharpen:capped`.

## Done when

- `uv run pytest` and `make check` pass.
- The commit is `feat(scheduler): sharpening measure, scorecard row and retro classes (issue-sharpening 06)`.
