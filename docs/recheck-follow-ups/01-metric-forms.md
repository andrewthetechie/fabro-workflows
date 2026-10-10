# 01. Finish the M1 and M3 metric forms (#3)

## Outcome
`ops/fabro-agent-tools.py` prints M1-M3 per repository, its event fixtures cover the four
cases issue #3 names, and the new-form baseline is recorded next to the old one. Issue #3
can close.

## Why
`b229455` already counts pipe-filter greps out of M1 and reports M3 by bytes and by whole
reads of 12 KB or more. The rest of #3 is still open. Without a per-repo line, the repo mix
decides the totals: womens-fantasy-sports was 56% of the 2026-10-08 window and 88% of the
2026-10-09 window. Without a recorded baseline in the new form, every recheck compares the
new forms with scratch numbers.

## Change
1. `report()`: add C1's `By repo` block after M3. Reuse the per-run `repo` that `load_run`
   already sets. Compute each repository's M1 (without pipe filters), M2 and the three M3
   forms from the same scoped visits as the totals, so that `--local-only` applies.
2. Add `ops/tests/fixtures/agent-tools/forms-a.jsonl` and `forms-b.jsonl` (C1). Build them by
   copying the event shapes in `run-fixture.jsonl`. Give them two different `origin_url`s.
   Write no real issue text.
3. Run the script over the 20 baseline runs (re-export them as in task 02, or use any copy you
   have). Write `docs/coder-tweaks/baseline-2026-10-02-forms.txt`: the command, the new-form
   lines, and one sentence on why the 12 KB numbers differ from `result-2026-10-08.txt`
   (that scratch count used coder and improve visits only).
4. Update the C7 table in `docs/coder-tweaks/00-overview-and-contracts.md` as C1 says.

## Acceptance
- On the fixtures, the pipe-filter grep is in the old M1 and not in the new one. Both halves
  of the `||` command count in both forms. The 2 KB read is not a whole read of 12 KB or
  more, and the 20 KB read is. The by-repo block has one line for each fixture repository.
- On the baseline runs the old-form lines are unchanged from `baseline-2026-10-02.txt`
  (shell grep 796 is the known difference), and the new-form numbers are the ones in the
  overview's *Verified facts*.
- `python3.11 -m unittest discover -s ops/tests` passes.

## Tests
Add to `ops/tests/test_fabro_agent_tools.py`: one test per acceptance bullet on the fixtures,
reading the report text the way `test_local_only` does.

## Depends on
Nothing.
