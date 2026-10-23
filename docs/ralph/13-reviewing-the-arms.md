# 13. Reviewing the arms

The operator's, like task 12, and repeated: every week of the trial (task 12, step 8) and
once more for the result (step 9). Each review is one file, `review-<date>.md` in this
folder. The final one also feeds `result-<date>.txt`.

## Outcome

A review compares `ralph` with `backlog` over the trial's runs so far, the same way each time:
the scorecard's numbers, a per-run read of every `ralph` run, and the `backlog` runs read from a
table. It also catches what the numbers hide, which is the reason it exists: the first review
found a `ralph` bug that had made 3 of its 5 runs look better than they were.

## Settled (operator, 2026-10-23)

| # | Decision |
|---|---|
| V1 | **The control arm is the scorecard's `backlog` column as it prints.** Its window is whole dates, so the first window holds the `backlog` runs of 2026-10-22 before the switch (21:28:06Z), and fast-path runs, which never go to `ralph`. The tables say how many runs are pre-switch. Do not filter them out. |
| V2 | **Depth: the numbers plus a per-run read.** Every `ralph` run is read (*The per-run read*). `backlog` runs are read from the runs table, and traced only when the table does not explain the ending. |
| V3 | **Tooling: `ops/fabro-trial-review.sh`, Mac-side and read-only.** It needs no deploy. Raw events never reach the Mac. |
| V4 | **Spend is estimated from tokens** (*The tool*). The OpenAI and OpenRouter dashboards are read only for the result file. |

## Steps

1. `git pull`. Then run the tool over the whole trial so far, and save the output in
   `.scratch/` (untracked):

   ```sh
   ./ops/fabro-trial-review.sh --traces > .scratch/trial-review-$(date -u +%F).md
   ```

   A weekly review also reads the last week alone (`--from <monday> --to <sunday>`) to see
   whether something changed. The cumulative window is the one O3 is judged on.
2. **Flags.** Read each flagged run first. A flag is a symptom, not a verdict: a timeout can
   be a slow box, and a run queued with `watch_checks` under a minute was the 2026-10-23 bug.
3. **The per-run read** (below), for every `ralph` run since the last review.
4. **`backlog`, by held reason.** Count the `backlog` PRs per held bucket and set them beside
   `ralph`'s. Trace a `backlog` run (`--trace <run id>`) only when its bucket is `no report`,
   `other` or `not read yet`, or when it opened no PR.
5. **Confounds.** Check each one, and write in the review whether it held:
   - **Kimi's quota.** When the *Provider failovers* table shows `kimi-k3` failing over, the
     arms' frontier stages run on different models: `backlog` falls to
     `orfallback:qwen3.8-27b` (which costs money), and `ralph` falls to `zai:glm-5.3` (flat
     rate). That changes both quality and spend.
   - **Open PRs.** A PR merged later still counts for the window of the run that opened it. A
     recent window's merge rate therefore rises as its PRs merge. Read again before you quote it.
   - **The pool mix.** Read the strata table: a rate that rose because one arm ran on the
     faster box is an artefact (00, C12).
   - **Pre-switch and fast-path runs** in the `backlog` column (V1). They matter only in the
     first window.
   - **Bugs.** Name every run that a bug affected, and say whether it stays in the counts
     (*When a review finds a bug*).
6. **Write `review-<date>.md`** with the template below. Add one line to task 12's *Log*
   that points to it.
7. **The verdict waits for ≥ 50 runs per arm** (O3). Before then, a review says *on track*,
   *a problem*, or *too early*, never who wins.

## The per-run read

For each `ralph` run, use its trace (from `--traces`), its row in the tables, and its PR:

```sh
gh pr view <pr> --repo <owner/repo> --json state,mergedAt,labels,statusCheckRollup,comments
```

Answer these, one line each, in the review's *Runs* table:

1. **Route.** Did it route as 00 designs (C3-C8)? `plan` → `pick` → the Iterations →
   `final_check` → a Refute round → reviews → `open_pr` → `merge_gate` → `watch_checks` →
   a report → `learn`. Any edge you cannot explain from 00 is a bug suspect. Confirm it in
   the graph before you call it a bug.
2. **The loop.** Iterations against Tasks (each extra Iteration is a miss), the highest rung,
   and any timeouts.
3. **The Refuter.** Each round's verdict and route. Did a Refute Task fix something real
   (the PR's *Refuter* section, and the diff)? Was a held `unverifiable` criterion one that
   no run could meet?
4. **The reviews.** Did review Tasks come from `error` findings worth a round?
5. **The ending, and who owns it.** Name one owner: *ralph design*, *ralph bug*, *shared
   gate* (hygiene, or a Refuter criterion that `backlog` hits too), *environment* (the CI
   queue, merge-queue, a provider quota), or *issue* (the issue asked for something no run
   could do).
6. **The PR.** Merged by the run, by merge-queue (its comment names the run), or by hand.
   Note any operator commits.
7. **Learnings.** Posted to the repository's `ralph learnings` issue, or skipped? Name any
   worth accepting.

## When a review finds a bug

Fix it now, ahead of the baseline. Follow `AGENTS.md`: start with a gate test in
`ops/gate_tests/` that fails, then the fix, then `make check`, then a push to `main`.
Then, in the review:

- Name the affected runs and what the bug changed for each one (the merge, the box-hours, a
  route).
- Report O3's numbers twice: with every run, and without the affected runs. The result file
  uses the run set the operator chooses. The trial window keeps its start date, because a bug
  in one arm does not move the other arm's runs.
- Add a symptom to the *Flags* section of `ops/fabro-trial-review.py` if the tool could
  have caught the bug, so the next review checks for it without having to know about it.

## The tool

`ops/fabro-trial-review.sh [--from D] [--to D] [--traces | --trace RUN]`. The defaults run
from 2026-10-22 to today (UTC, both days included). A window holds the runs *dispatched* in
it, as the scorecard's does. It prints:

| Section | Source | What it counts |
|---|---|---|
| Scorecard by workflow | `fabro-scorecard.sh --by-workflow` | The arms' cards and the repo × pool strata, unchanged (V1). |
| Runs | `run_history`, `run_summary`, `run_stage_stats`, `pr_facts` | One row per run. *ending*: the node that routed into `learn_prep` (`ralph`) or into `review_merge.done` (`backlog`). *fate* and *held*: `scorecard.merged_by_workflow` and `scorecard.held_bucket`, imported from the deployed scheduler, so they cannot differ from the scorecard. *refute*: the verdicts in order (`P`/`F`). *iter*: the Iterations (and visits per rung, t1/t2/t3/t4), the Refute visits, and the Tasks by `source`. |
| O3 | the same | PRs merged by the workflow with no operator commit, per run; box-hours (leases) per such merge; and `ralph`'s difference from `backlog`, against the +20% guardrail. |
| Flags | the same | `ralph` runs to read first: queued with `watch_checks` under a minute, timeouts, misses, a Task above rung 1, a loop that ran out, post-PR Iterations, not summarized yet. |
| Where each lease went | `run_stage_stats` | Minutes per stage group for each `ralph` run. A stage's time counts only the attempt that finished, so the time of a timed-out attempt that was retried shows under *other*, and *timeouts* names the stage. |
| Longest nodes per arm | `run_stage_stats` | The ten nodes with the most wall time, with the time on the coder box. |
| Provider failovers | `run_failovers` | By arm, node and route. Kimi's quota shows here (*Confounds*). |
| Hosted spend (estimate) | `run_model_usage` | Tokens by arm and model, priced three ways: fabro's own cost when it has one (OpenRouter); ADR 0013's `gpt-6-luna` rates otherwise (in `RATES`); $0 for flat-rate plans (Kimi, z.ai) and the local boxes. `UNPRICED` marks a new per-token model: add its rates to `RATES`. The monthly projection covers `run_history` only, so `pr-review`, `dependabot`, `arch-review` and hand fires are missing from it. |
| ralph routes (`--traces`) | `fabro events -p`, filtered on the host | Each `ralph` run's edges without the loop's plumbing: `→ iterate_tN` gives the rung, `→ record` gives `check`'s outcome, and `✗` marks a failed or timed-out attempt. |

The `.py` file runs inside the scheduler container (`docker exec -i fabro-scheduler python
-I -`) and opens the database with `mode=ro`. `fabro events -p` output is only the
human-readable lines, a few dozen KB per run. It is never the raw event JSON that filled the
disk on 2026-10-11.

## The review file

`docs/ralph/review-<date>.md`:

```markdown
# ralph vs backlog: review of <date>

**Window:** <from> to <to> (dispatch dates, UTC). **Runs:** backlog N (M before the switch),
ralph N. **Verdict so far:** on track | a problem | too early (O3 needs ≥ 50 per arm).

## Summary
Three to six bullets: what changed since the last review, and what the operator must decide.

## O3
The O3 table, the strata that matter, and the confounds (step 5), each one marked as holding or not.

## Runs
| run | issue | ending | owner | loop | Refuter | reviews | PR | note |
One row per ralph run (the per-run read).

## backlog by held reason
| bucket | backlog | ralph |

## Bugs and fixes
What was found, the commit, the affected runs, and O3 with and without them.

## Spend
The estimate and the projection against $100.

## Next
What the next review must check.
```
