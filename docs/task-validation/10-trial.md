# 10. The womens-fantasy-sports trial

**Switch time:** **2026-10-15T01:11:50Z** (womens-fantasy-sports#2088 squash-merged as
`4fa6bb8`). Baseline window: 2026-10-14 to 2026-10-14 (C10: never before 2026-10-14; that day
holds 56 womens-fantasy-sports runs, at least 15). After window: 2026-10-15 to 2026-10-21;
run it on the morning of 2026-10-22 (UTC).

**Task 09 PR:** https://github.com/andrewthetechie/womens-fantasy-sports/pull/2088 — the switch
is its merge time (UTC).

## Outcome
`docs/task-validation/result-<date>.txt` says whether Task validation met issue #26's two
targets on womens-fantasy-sports (C10). ADR 0025 and this series' status say what follows.

## Change
1. Seven days after the switch, run the C10 commands for both windows and save the output.
   If either side has fewer than 15 runs, extend the after-window a day at a time and say so.
2. Record:
   - (a) per-visit `validate` minutes, median and p90, before and after, and the after-window split by mode (scoped against full)
   - (b) the red final rate before and after
   - the full visits by reason: a high `unowned_test` or `index_unavailable` count is a configuration or index fault to fix, not a design result
   - the untested and unchecked counts
   - the preparation seconds per scoped visit (`task_validation_prepare_s`); if preparation is over half of a scoped visit, that is the next lever
   - (c) box-hours per merged PR, before and after
   - the coder and rework stages' minutes p50 before and after, and the `task_check` calls
     per run (M-run, `ops/fabro-agent-tools.py`): where the test time went
3. **Pass** ((a), (b) and (c) all met; C10):
   - mark ADR 0025 `accepted (<date>)`
   - update the `docs/README.md` row and this series' status
   - comment the result on #26 and close it when task 11 is done
4. **Fail:**
   - open a PR deleting the Task validation keys from womens-fantasy-sports' `test.toml`. That is the rollback, and needs no workflow commit.
   - record the cause in the result file: which tests failed late, and whether the selection should have caught them
   - leave ADR 0025 `proposed` with a status note

## Acceptance
The result file is committed, and the statuses match it.

## Depends on
09, plus seven days of runs.

## Result (2026-10-21): pass on cause

Read on 2026-10-21 at 20:07Z instead of 10-22, by operator decision. The after-window is
2026-10-15T01:11:50Z to 10-21T20:06:55Z, with 57 runs on each side. The numbers are in
`result-2026-10-21.txt`.

- (a) passed, 6.03 → 0.48 min. (b) passed, 0% → 0%.
- (c) failed as written, 1.74 → 8.77 box-hours per merged PR. The cause was measured:
  Task validation moved about 2.8 box-hours of test time into the coder stage and saved
  about 9.2 in `validate`. The rise came from other changes in the window.
- The operator chose to treat it as a pass: no rollback, and ADR 0025 is accepted. #26 got
  the result as a comment and stays open until task 11 is done.

