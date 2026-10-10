# 10. Measure skeleton-first decomposition (#5 acceptance)

## Outcome
`docs/recheck-follow-ups/result-skeleton-first-<date>.txt` compares writers-app coder visits
before and after task 08, and ADR 0021's status says whether the rule stays.

## Why
#5's acceptance is a measurement over 10 or more writers-app `architecture` runs. writers-app
had no run in the 2026-10-09 window, so the wait may be long. Do not report on fewer runs.

## Change
1. When 10 or more writers-app `backlog` runs for `architecture` issues have finished after
   task 08's push, export them with task 02's script. A run's issue labels are in its
   `issue.json` input. `--outcomes` does not print them.
2. Measure the same way as `measure-2026-10-10.txt` section 1. The scratch script is
   `.scratch/recheck-follow-ups/drafts.py`. If it is gone, that file has the definition.
   Report draft visits and draft tokens by task kind (skeleton, bodies, port, other), and visits
   over 10 minutes to the first edit.
3. From `--outcomes`: M8 (first pass, reworks per task), and how many runs filed a Remainder
   issue. Compare that rate with writers-app architecture runs before task 08.
4. For each skeleton task: did a later port change one of its signatures? (`git log -p` on
   the core file in the PR.) A changed signature means the skeleton missed a port's need.

## Acceptance
- The coder draft-visit share and the over-10-minute share are both below the window in
  `measure-2026-10-10.txt` (45% and 9 of 42), and M8 is no worse than
  `result-2026-10-08.txt`'s writers-app line (93% / 0.12).
- The Remainder rate is reported, with the count of runs where the budget fell between a
  skeleton and its bodies.
- If the shares do not fall, the result names the next lever (ADR 0021, *Considered
  options*: a hosted model for core tasks).

## Depends on
08, and 03 for `--outcomes`.
