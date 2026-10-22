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

(empty)
