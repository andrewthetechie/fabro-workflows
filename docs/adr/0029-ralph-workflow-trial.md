# A second issue workflow, `ralph`, is trialled against `backlog` and owns its merge stage

**Status:** proposed (2026-10-21). Every decision below is the operator's, from the
planning session of 2026-10-21. The task series is `docs/ralph/`. It becomes accepted or
rejected by the trial's result (task 12).

`backlog` was built to reproduce the operator's earlier workflow: decompose, then per task a
dossier, a coder, a per-task LLM review and a rework ladder, then an extra review, then the
shared merge phase with its own fixers. The first six weeks proved that fabro can run it.
They also showed where it spends: `improve` was 24% of all stage time (#25), every task
pays an LLM review, and the merge phase fixes review findings, Refuter findings and red CI
with three separate hosted fixers that never return to the coder that wrote the code.

`ralph` tests a different shape: a plan written once, a loop of fresh coder sessions that
each take one task and pass a mechanical gate, and an adversarial check when the loop says
it is done. Every finding from any check, the Refuter's, the reviewers', CI's, becomes a new
task for the same loop.

## D1. A new package beside `backlog`, not a lane inside it

`ralph` is an eighth package, `.fabro/workflows/ralph/`. It shares no graph with `backlog`
and imports neither `_shared/` phase. `backlog` is not changed by this series.

Rejected:
- **A lane inside `backlog`** (the fast-path pattern). The loop, the Refute rounds and the
  merge stage replace most of `backlog`'s nodes; a lane would double the graph's size and
  make every `backlog` invariant conditional on the lane.
- **Changing `_shared/review-merge/`.** It is the control arm's merge phase. A mode added
  mid-trial changes the thing the trial compares against.

## D2. A whole-package trial, by issue parity, after the plan trial closes

Even issue numbers go to `ralph`, odd ones to `backlog`, when the scheduler's `ralph_share`
is 50. A `fast-path` issue always runs `backlog`. The trial compares the whole package, so
it cannot say which of its parts made a difference. It starts only after the plan trial
(#25) closes, so the control arm is one `backlog` with `plan` and no `improve`, and after
the Task validation and Refuter-evidence measurements (2026-10-22, 2026-10-23) report.

The primary metric is PRs merged with no operator commit, per run. The guardrails are
box-hours per merged PR (no worse than +20%) and total hosted spend under $100 a month.

## D3. The Refuter is inside the loop

When every task has passed, the Refuter (gpt-6-luna) is told the work is not done and
tries to prove it. Each finding it can prove becomes a task. A run has 3 Refute rounds.
Findings the Refuter still holds after the last round are reported as Unmet criteria, and
the PR is not auto-merged. The two-axis review runs after the Refuter passes, and its
blocking findings also become tasks, inside the same 3 rounds.

This replaces `backlog`'s per-task review, extra review, `review_fix` and `refute_fix` with
one loop and one budget. The bet is that cheap iterations with a mechanical gate, plus an
expensive adversarial check at the end, land more PRs per box-hour than an LLM review on
every task.

## D4. `ralph` owns its merge stage, and merges before the box moves on

The run merges its own PR. It holds the Coder lease through CI, as `backlog` does (ADR
0006), so the box takes no other issue until the PR is merged or handed off. A later issue
then starts from a `main` that has this one's work. `merge-on-green` and `merge-queue` are
the fallback for a PR whose 60-minute merge deadline runs out: they are never the primary
merger.

Red CI and a base conflict become work for the loop, not for a hosted fixer. The merge
stage's command nodes are copies of the shared phase's, and an offline gate in `make check`
fails when a copy drifts from its original, so a divergence is a decision, never an
accident.

Rejected:
- **Importing `_shared/review-merge/` unchanged.** The reviews and the Refuter would run
  twice, and the second time their fixers would edit code the loop never saw.
- **Ending the run at PR open (the fast lane's queue mode).** The box would be free sooner,
  but a dependent issue could start before its prerequisite merged and re-implement it.

## D5. A run proposes Learnings, a human accepts them

Each run with at least one Iteration ends with a short stage on the coder box that writes
0–5 **Learnings**, each with evidence. They are posted as one comment on a rolling `ralph
learnings` issue in the target repository. No run edits `AGENTS.md` or any other file to
apply one. The target repositories are private and this one is public, so Learnings are
never written here.

## Consequences

- The scheduler can fire two workflows. Its run history, scorecard, Retro and probe learn
  to tell them apart (`docs/ralph/` tasks 09, 10).
- There are now two copies of the merge stage's shell. Every fix to a shared merge node
  needs a decision about the copy, which the drift gate forces.
- The trial adds Refuter visits: up to 3 per `ralph` run, against 1 or 2 for `backlog`. On
  gpt-6-luna that is about $1–2 a day at today's volume.
- If `ralph` wins, `backlog`'s standard lane is retired in its own series and `backlog`
  keeps the fast lane. If it loses, `ralph` is deleted, and any single part that looked
  good is proposed on its own.
