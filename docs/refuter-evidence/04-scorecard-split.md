# 04: the scorecard split

## Steps

In `ops/scheduler/src/fabro_scheduler/`:

- `summary.py`: add `unverifiable_only: bool | None = None` and
  `ci_credited: int | None = None` to `RefuteVerdict`. Read them from the same
  `context_updates`, accepting a bool or the strings `"true"`/`"false"` (as `not_met` does
  with `_bool_text`) and an int or a digit string. If a key is absent, use `None`.
- `store.py` (about line 1102): add `"unverifiable_only"` and `"ci_credited"` to each stored
  dict. The column is JSON text, so no migration is needed. Update the column comment at
  about line 218.
- `scorecard.py`:
  - In `RefuteLine`, replace the single `first_fail_other` bucket with three:
    - `first_fail_unverifiable_only` (`not_met` is False and `unverifiable_only` is True);
    - `first_fail_defects` (`not_met` is False and `unverifiable_only` is False);
    - `first_fail_other_unknown` (`not_met` is False and `unverifiable_only` is None, which
      covers runs from before this change).

    Keep `first_fail_unknown` as it is.
  - Add `first_ci_credited: int`, the sum of `ci_credited` over first verdicts where it is
    not None, and `first_ci_credited_runs: int`, the number of runs where it is not None.
  - Change the table line (about line 669) to:
    `| Refuter first verdicts: pass / not_met / defects / unverifiable-only / other / invalid | ... |`
    where "other" is `first_fail_other_unknown + first_fail_unknown`. Keep the fix-turn text.
  - Add one line after it:
    `| Refuter CI-credited criteria (first verdicts) | <first_ci_credited> over <first_ci_credited_runs> runs |`.
  - Search the whole scheduler package for every other use of `first_fail_other` (for
    example the Metrics tab template or JSON output) and update each one consistently.
    `grep -rn first_fail_other ops/scheduler` must return nothing when you finish.

## Tests

Update `ops/scheduler/tests/test_summary.py`, `test_scorecard.py`,
`test_store_history.py` and any other test that breaks. Add cases for:
- the three new buckets;
- an old stored row with only `{verdict, not_met}`, which lands in "other";
- `ci_credited` summing.

Run the scheduler tests the way `ops/check.sh` runs them (read it to find the command).

## Done when

`make check` passes. Commit:
`feat(scheduler): split the Refuter's other fails and count CI-credited criteria (refuter-evidence 04, #40)`.
