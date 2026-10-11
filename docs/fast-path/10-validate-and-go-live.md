# 10. Validate, hand-fire, go live, measure (operator)

## Outcome

The fast lane and the merge queue are live in all four repositories. Hand-fired runs have
shown each path once on real PRs. The four schedules are enabled. Seven days later,
`docs/fast-path/result-<date>.txt` compares the fast lane with the standard lane, and ADRs
0023 and 0024 are accepted or amended.

## Why

#23's "done when", adapted to this design: a batch of mechanical issues lands with less box
time per closed issue, and the series measures how much less. Every gate in tasks 02-08 is
offline. This task checks the live behaviour that no fixture can show.

## Change

Do the steps in order. Stop at the first step that fails, and record what it showed.

### 1. Gates

`make check` and `make check-host` from a clean checkout at `origin/main`. The node and edge
counts equal the `AGENTS.md` baselines from tasks 02-05.

### 2. A quiet sweep

Fire `merge-queue-jelly-swipe`'s `api:manual` trigger with no body (the path that
`fire-pr-review.sh` uses; `docs/dependabot/10`, step 5). No PR carries `merge-on-green` yet.
Check that:

- the run reaches `finish` with `plan.json` = `[]`;
- no `fabro/run/*` branch was pushed, and no PR was opened;
- Discord got no message;
- its time to the first node and total time are recorded. If S6 was skipped, this is S6.

### 3. One fast-lane issue, end to end

Pick one small open issue in womens-fantasy-sports or lawncare-saas that a one-commit change
closes. Add `fast-path` and `agent`. When the scheduler dispatches it, follow
`fabro events <run> -p` and check that:

| # | Check |
|---|---|
| a | `route_lane` published `lane=fast`. `decompose`, `improve`, `excerpts`, `extra_prep` and `watch_checks` never ran. |
| b | `review` ran once or more, and the PR opened after `integrate`. |
| c | `pr_handoff` wrote `queue`. The merge phase ran `standards`, `spec`, `review_fix`, `validate`, `hygiene`, `refute` and `merge_gate`, then `report_queued`. |
| d | The PR has the review comment, with the C4 marker on line 1, and the label `merge-on-green`. Discord got the `queued` message. |
| e | The run ended `succeeded`, and the scheduler released the coder box at that moment (the scheduler page). Record the lease's duration. |

When the PR's checks are green, fire that repository's `merge-queue` row by hand. Check that
the PR is squash-merged with the PR title as the subject and the body block as the body,
that the issue closed and lost `Review`, and that Discord got a `Merged #N` summary.

### 4. The strict repository

In jelly-swipe, send two small issues down the fast lane and let both reach `merge-on-green`.
Fire the queue by hand after both are green. One merges, and the other becomes `BEHIND`.
Fire again: the second PR gets a C5 marker and a rebase. After its CI is green, fire again,
and the PR merges. Record the rebase method used (S2) and the commit authors on the rebased
head.

### 5. The hand-off paths

On the S2-style scratch draft PR (recreate it), add `merge-on-green` by hand and fire the
queue. The PR is handed off: a C6 comment saying there is no approval, the label is
removed, and `ai-review-needs-human` is added. Close the PR and delete its branch.

### 6. Go live

Enable the four schedules with `ops/fabro-automation-schedule.sh` (extend it first if it
does not accept `merge-queue` rows). Record the time. Then add `fast-path` to a first batch
of 10 mechanical issues of the #23 kind, all in one repository, with `agent` on them.
Increase the batch only after they land.

### 7. Watch for one day

- `fabro-monitor.sh` still fires no false C3, and it still sees every `backlog` run.
- The retention job ran once and freed `merge-queue` runs older than 7 days (it frees
  nothing on the first day; check its log line).
- Discord: summaries only on ticks that changed something.

### 8. Measure after 7 days

Write `result-<date>.txt` from `ops/fabro-export-runs.sh` and the scheduler's run history:

- Fast lane: the number of runs, and the median and p90 of run time and coder-lease time.
  Hosted tokens per run. Counts of `queued`, `watch` (over the ceiling), demoted and
  rescued runs.
- The queue: the time from `queued` to merge (median, p90), merges per day per repository,
  and the hand-offs by reason (retryable and not). Rebases per merge in jelly-swipe.
- The comparison: lease hours per closed issue, fast lane against the standard lane for
  diffs of 200 lines or fewer over the same 7 days. #23's baseline is p50 1.3 h for ≤ 20
  lines and 1.6 h for 21-200 lines.
- Wrong outcomes: any queue merge that a human reverted, and any merged PR whose `main` CI
  went red afterwards.

### 9. Close out

Mark ADR 0023 and ADR 0024 accepted, or amend them with what the result showed. Update 00's
status line and the `docs/README.md` row. Comment on #23 with the result and a link to it.
Append a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`.

## Acceptance

- Steps 2-5 each passed once, with the run ids recorded in 00's status line.
- At least 10 `fast-path` issues closed through queue merges.
- `result-<date>.txt` exists and answers every bullet of step 8.

## Tests

None beyond tasks 02-08. This task is live validation.

## Depends on

09.
