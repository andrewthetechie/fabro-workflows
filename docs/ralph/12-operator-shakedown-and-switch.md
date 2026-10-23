# 12. Operator: push, shakedown, switch, measure, end

The operator's task. Each step records its result in this file's *Log* and in the deployment
log on the Mac (dated section).

## Steps

1. **Review** the local commits of tasks 01-11. `make check`.
2. **Preconditions.** P1 and P2 in 00 hold. If P1 has not landed, task 10's precondition
   stopped it; land the plan trial's ending first.
3. **Push** to `main`, then `make check-host`. Record the `Ralph (N nodes, M edges)` baseline
   and its one expected warning in `AGENTS.md` (P3). `ralph` is now on `main` but nothing
   fires it.
4. **Deploy the scheduler** with `SCHEDULER_RALPH_SHARE=0` (`make deploy-scheduler`), and
   `make deploy-scripts` for `fabro-fire-ralph.sh`. Check `/` and `/history` show the
   workflow column, every row `backlog`. Create no `ralph-<repo>` automation row: the
   scheduler takes the environment from its own repository config, and `fabro-fire-ralph.sh`
   falls back to the `backlog-<repo>` row, which names the same image.
5. **Shakedown**, one manual run per repository, each on an issue the operator picked as
   ordinary (not `fast-path`, not architecture): `~/bin/fabro-fire-ralph.sh <owner/repo>
   <issue>`. For each run, check with `fabro events <run> -p`:
   - every agent stage's Input receipt is accepted by its gate;
   - `pick` → `excerpts` → `iterate_t1` → `check` → `record` → `pick` routes, and a red
     `check` gives a miss and `## Last attempt`;
   - at least one Refute round across the four runs turns a finding into a Task (if none does,
     fire a fifth run on an issue with a known gap);
   - the reviews run, the PR opens, `merge_gate` decides, and the run merges or reports;
   - `learn_post` comments on the repository's `ralph learnings` issue, or skips with a reason;
   - the Iteration times against the 60m timeout, the `plan` (20m), `standards` (25m),
     `spec` (15m) and `refute` (15m) times against theirs, and `pick` and `merge_prep` (5m
     each, set by the build without a measurement); re-time in a commit if any is wrong;
   - the Discord notices: one on `open_pr` and one on the merge-stage report.
   Shakedown runs are Manual fires, so they are outside `run_history` and the trial.
6. **Switch.** Set `SCHEDULER_RALPH_SHARE=50` in the compose file, `make deploy-scheduler`.
   Record the switch time: the trial window starts there.
7. **First day.** Read `./ops/fabro-scorecard.sh --by-workflow --from <switch>`: both arms
   have runs; `ralph` runs end the way the shakedown did. The kill switch is
   `SCHEDULER_RALPH_SHARE=0` and a scheduler deploy: in-flight `ralph` runs finish.
8. **Weekly**, until ≥ 50 runs per arm: the by-workflow scorecard; OpenAI and OpenRouter
   spend from their dashboards against the $100 total (the scorecard does not price them);
   Kimi failovers per workflow (00, *Risks*); the `ralph learnings` issues.
9. **Result.** Write `result-<date>.txt` here: both arms' cards, the strata, the primary
   metric and both guardrails, per O3. Choose O24's outcome and record it in ADR 0029 (status
   accepted or rejected, with the date and the numbers), then plan the follow-up series.

## Log

**2026-10-22.** Steps 1-4 and 6 done; step 5 skipped.

- 1: reviewed tasks 01-11 and fixed them (bdc5a0d..8e38f94); `make check` green.
- 3: pushed 8e38f94. The first `make check-host` failed: `fabro validate` rejected ralph
  (`stop_reason` had only conditional edges). Fixed in c0b9e5e, which also adds R12 to
  check it offline. `make check-host` then passed: `Ralph (51 nodes, 135 edges)`, one warning
  (`issue_number` in `claim`). `fabro preflight` with the inputs bound fails only on the Kimi
  weekly quota, the same as `backlog`'s.
- 4 and 6 together: the operator chose to start the trial without a shakedown. b583473 set the
  compose default to 50, and `deploy-host.sh scripts compose scheduler verify` deployed it
  (verify all ok). **Switch: scheduler started 2026-10-22T21:28:06Z, the trial window's
  start.** Fabro was not restarted; the two in-flight `backlog` runs (lawncare-saas#2962,
  #2963) kept their leases.
- 5: not run. The first trial runs are the shakedown, so step 5's checks (receipts, the
  loop's routes, a Refute round, the merge stage, `learn_post`, the timeouts, the Discord
  notices) apply to them. They count in `run_history`, unlike manual fires, so read the
  first `ralph` runs before trusting the arm's numbers. The first queued candidate was
  lawncare-saas#2970.

**2026-10-23, first trial run** (step 5's checklist on run `01M5JZ085993`, lawncare-saas#2970,
a scout flaky-test issue; succeeded in 57.7m, PR #2982, ending `report_blocked`).

- Receipts: every gate accepted its agent's output; `io-stage` and `git-guard` ran.
- Loop: `pick` → `excerpts` → `iterate_t1` → `task_block_gate` → `autofix` → `check` →
  `record` → `pick` routed as designed. One Task, green on rung 1, so a red `check` (a miss and
  `## Last attempt`) was not exercised.
- Refute: verdict `fail` on one `unverifiable` criterion, routed `hold` (not exhausted) to the
  reviews. No finding became a Task, so that is still unexercised. The criterion is scout's
  "The PR description names the cause … and cites the failures above": both arms build the PR
  body from the issue's first paragraph, so no run can meet it, and the Refuter was right that
  it was not met. Not #52's class (that is process criteria). The text comes from
  `ops/fabro-io/src/scout/text.rs`.
- Reviews: `standards` one `info` finding, `spec` approve; `review_gate` passed, `open_pr`
  opened #2982, `merge_gate` blocked it on diff hygiene (`skips_added=1`): the plan's own
  acceptance asked for a Docker-gated `skipif` test, which hygiene blocks in either arm.
- `learn_post`: skipped with a reason; `learn` proposed no Learning (`learnings: []`).
- Times against timeouts: `plan` 11.2m/20m, `iterate_t1` 22.6m/60m, `final_check` 8.3m/20m,
  `refute` 1.6m/15m, `standards` 5.8m/25m, `spec` 3.7m/15m, `learn` 3.5m/10m, `pick` <1s/5m,
  `merge_prep` 2s/5m. No re-timing needed yet.
- Discord: the `open_pr` and `report_blocked` hooks ran (00:20:38Z, 00:20:49Z).
- Kimi's weekly quota failed `plan`, `standards` and `spec` over to glm-5.3, as expected.
