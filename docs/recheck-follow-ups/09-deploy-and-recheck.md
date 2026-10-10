# 09. Deploy, verify live, and run the first recheck with the new tools (#13, #14)

## Outcome
Tasks 04 and 05 are verified on live runs. The next coder-tweaks recheck is done with
`fabro-export-runs.sh` and `fabro-agent-tools.py --outcomes` and no scratch script. That
satisfies the *Done when* of #13 and #14. The old metric forms are then removed (D12).

## Rollout
1. Push 04 and 07 (graph changes, live on the next fire).
2. `make deploy-scheduler` and `make deploy-scripts` for 05, from a clean checkout at
   `origin/main`. Then `make verify-host`.
3. Append a dated entry to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` for each step.

## Verify
- The first scheduler run after step 2: `GET /runs` shows `labels.workflow_sha` equal to the
  `main` SHA at dispatch, and the scheduler log line shows the same `sha=`.
- The first `backlog` run that reaches `report_blocked` or `mark_needs_human`:
  `stage.completed.properties.context_updates.merge_block_reason` holds the same text as the
  PR comment's reason. Its stage did not fail on the routing schema.
- The first `backlog` run after step 1: every `autofix` visit publishes `autofix_ran` and
  `autofix_rc`. Compare with the repository: writers-app and womens-fantasy-sports have a
  `fix.sh` (`measure-2026-10-10.txt`), and lawncare-saas has one only if lawncare-saas#2747
  has merged.
- The first `issue-triage` run after task 07 runs to the end.

## Recheck
When at least 15 `backlog` runs have finished after step 2:
1. `ops/fabro-export-runs.sh --since <end of the previous window>`.
2. `python3.11 ops/fabro-agent-tools.py --local-only --outcomes <OUTDIR>/*.jsonl`.
3. Write `docs/coder-tweaks/result-<date>.txt` in the form of `result-2026-10-09.txt`. Its
   outcome section comes from step 2 alone. List anything you still had to get another way,
   and open an issue for each.
4. Then remove the old M1 and M3 count-only lines from `fabro-agent-tools.py` and its tests
   (D12), in a separate commit that cites the result file.

## Acceptance
- Each *Verify* bullet is recorded in the deploy log with its run id.
- The result file says it needed no scratch script, or lists what it needed.
- ADR 0020's status becomes accepted if both are true.

## Depends on
02, 03, 04, 05, 07.
