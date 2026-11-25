# Task 14: weekly checks and the result (operator)

Method: 00, *The experiment*.

## Weekly, from the switch

`./ops/fabro-trial-review.sh --arms ralph,ralph-v2 --from <switch date>`, then:

1. The stop rules (`trial.lease_stop`, `merge_stop`, `miss_stop`). A stop is the kill switch, then a read of why.
   `miss_stop` is not a result: fix the misses and restart the window.
2. Every PR v2 held because a Task was blocked by review: was the finding real? Record `real`, `wrong` or
   `unclear` per PR in `weekly-<date>.txt` here. Numbers and PR ids only: lawncare-saas and
   womens-fantasy-sports are private.
3. Five disputes: withdrawn and upheld, same record. This is the only check on the box reviewer's precision.

Nothing in either arm changes while the window runs, except an outage fix to both. Record any with its time.

## The result, at four weeks or 150 finished runs an arm, whichever is later

1. Merge, quality and guardrail from `TrialLine`, and per stratum (repository x pool).
2. Quality twice: as defined, and the Refuter's fields alone. The share of exhausted runs per arm beside it.
3. **The A/A check.** Before the trial, with `ralph` on both parities (2026-11-12..11-24), even issues merged 43
   of 91 and odd 51 of 93. Report the same two rates over the 14 days before the switch. Report v2's merge result
   as defined, and beside it the difference-in-differences. Only the first decides.
4. Cost: lease-hours per zero-touch merge, box minutes by new stage, z.ai credits a run, `terra` uses.
5. Did v2's parts earn their place: rejection rate by round; the share of round-1 hosted blockers in a file a Task
   review had flagged; Tasks blocked and how many were real; edge cases planned and covered.
6. Write `result-<date>.txt` here. Set ADR 0034 to accepted or rejected with the date.
   - **v2 wins**: `SCHEDULER_RALPH_V2_SHARE=100`; plan `ralph`'s removal as its own series (R10's v2 rows then
     point at the shared originals).
   - **v1 stays**: the share to 0; delete the package, its tests, its checker rows and its scheduler names.
   - **Mixed**: the operator decides from the file.
7. Close #87 with the result.

Follow-ups that depend on it: dropping the whole-branch box lenses (about 23 box-min a run), the CI Iteration
cost (18 min a run), undraining the second slots.
