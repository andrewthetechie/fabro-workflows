# 10. The womens-fantasy-sports trial

**Switch time:** _pending: the operator must merge the PR by hand (it trips `config_changed`)._

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
   - the unchecked count
   - the preparation seconds per scoped visit (`task_validation_prepare_s`); if preparation is over half of a scoped visit, that is the next lever
   - box-hours per merged PR, before and after, for context
3. **Pass** (both (a) and (b) met):
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
