# A `fast-path` label sends a small issue through a short lane of `backlog`

**Status:** proposed (2026-10-11). The operator chose this design instead of the batch lane
that issue #23 proposes. D1 to D5 are operator decisions. D6 and D7 were drafted while
planning, and the operator can veto them until task 03 of `docs/fast-path/` lands. The task
series is `docs/fast-path/`. ADR 0024 merges the PRs that this lane produces.

On 2026-10-10 about 360 issues were filed in one target repository. About 300 of them are
one-file mechanical changes, such as "remove unsafe type assertion in `<file>`". Each one
goes through the full `backlog` path: `decompose`, then `improve` and `coder` per task, the
per-task `review`, three extra-review rounds with `extra_decompose`, and the shared merge
phase. The merge phase holds the coder box while `watch_checks` waits for GitHub CI, for up
to 50 minutes. A run that changes 20 lines or fewer has a p50 of 1.3 h (#23). At that rate
the 300 issues use about a week of both coder boxes.

We add a second lane to `backlog`. A human selects it with a label before the run starts.

## D1. A human selects the lane with the `fast-path` label

A human adds the label `fast-path` to an issue, next to `agent`. The scheduler dispatches
the issue as it dispatches any other issue, and it does not read the label. A new command
node, `route_lane`, runs after `prep`. It reads the labels from `/tmp/fabro/issue.json`,
which `claim` fetched, and publishes `lane = fast` or `lane = standard`. No agent decides
the lane, and no agent can change it.

Triage never adds `fast-path`. `apply_split` removes it from the children of a split issue,
because an issue that needs a split is not a one-commit change.

## D2. One task, with no `decompose` and no `improve`

In the fast lane, `route_lane` writes `tasks.json` itself. It contains one task, built from
the issue title and body, with `source: "fast-path"`. `next_task` then sends that task
directly to `coder`. `improve`, `improve_gate` and `excerpts` do not run. The coder has no
task dossier, and its prompt already handles that case (`_io/manifest.json`:
`task-context.md` and `task-code.md` are optional inputs of `coder`). `next_task`'s mainline
refresh and `resolve_merge` stay.

## D3. The per-task review and the rework ladder stay

The operator decided this on 2026-10-11. The task goes through
`coder → autofix → prep_review → validate → review → integrate`. A red `validate` or a
rejected review climbs the same rework ladder as in the standard lane. This gives the local
coder its own correction loop before the PR opens.

## D4. No extra review

In the fast lane, `next_task` goes to `open_pr_prep` when the task is done. `extra_prep`,
`standards`, `spec`, `quality` and `extra_decompose` do not run, so the run files no
follow-up tasks.

## D5. The merge phase reviews the PR and queues it, and the run ends

`pr_handoff` writes `/tmp/fabro/merge_mode = queue`. The shared merge phase runs as it does
for every `backlog` PR: `standards`, `spec`, `review_fix` with direct fixes, `validate`, the
diff-hygiene counters, the Refuter and `merge_gate`. The Refuter stays. Mechanical changes
are the kind of work where it can tell clearly that the issue is done. When `merge_gate`
passes, the phase does not watch CI and does not merge. A new node, `report_queued`, posts
the review comment with an approval marker, labels the PR `merge-on-green`, and ends the
phase with `review_merge_outcome = queued`. The run then ends and the scheduler frees the
coder box. ADR 0024 merges the PR when its checks are green.

## D6. A diff above the ceiling is merged in the run, as in the standard lane *(drafted)*

`pr_handoff` counts the PR's changed lines and files with `git diff --numstat`. It skips the
paths that match the default `exclude_paths` of the diff-hygiene counters, so lockfiles do
not count. The ceiling is **200 changed lines or 10 files**. Above it, the label was wrong
for this issue, so `pr_handoff` writes `merge_mode = watch`. The merge phase then watches CI
and merges in the run, as it does for a standard PR, and `pr_handoff` posts one comment on
the PR that gives the reason. A PR from the rescue gate's `[P] Accept partial` path also
gets `watch`, because a task that did not pass review must not merge without a run that
watches it. The ceiling is a deterministic check of the label's premise ("one small
commit"). #27 proposes a line cap for the standard lane, and its data can tune these numbers.

## D7. The lane demotes a fast task that the ladder cannot land *(drafted)*

`rework_router` sends a standard task to `rescue_brief` at round 6, and the run waits at the
rescue gate for up to 4 hours while it holds a coder box. In the fast lane, round 6 goes to
a new node, `demote_lane`. It removes `fast-path` from the issue first. If that call fails,
the node stops, so the issue can never go back into the fast lane by mistake. It then
replaces `agent-in-progress` with `agent` and posts the last findings on the issue. The run
ends with no PR. The scheduler dispatches the issue again in the standard lane, where
`decompose` and `improve` can split the work. A label that turned out to be wrong then costs
one short run instead of a 4-hour wait at the gate.

## Rejected alternatives

- **The batch lane of #23** (one run and one PR for up to 20 same-rule issues). The run
  must revert, comment on and requeue each issue that fails. The Refuter's inputs grow with
  N, and #23 leaves open how large N can be. A red CI blocks the whole batch. The fast lane
  gives most of the saving per issue, because it removes the stages and the CI wait that set
  the floor. It keeps one issue, one PR and one rollback. A batch lane can come later, with
  this lane's measurements as its baseline.
- **A separate package.** It would copy `claim`, `prep`, the coder, the rework ladder,
  `open_pr_prep`, `open_pr` and the rescue gate, which is about 1,500 lines of `backlog`.
  The two copies would drift apart. The lane adds about four nodes and some edge conditions
  to the graph that already runs.
- **The agent decides the lane** (triage or `decompose` classifies the issue). The operator
  rejected this. A human decides before the run starts.

## Consequences

- `backlog` gains `route_lane` and `demote_lane` plus some edge conditions. Its node and
  edge baselines in `AGENTS.md` change, as do those of every graph that imports the merge
  phase, which gains `report_queued`.
- A fast-lane PR gets no `ci_fix`. When its GitHub CI goes red, ADR 0024 reruns the failed
  jobs once and then gives the PR to a human. `validate` and `open_pr_prep` have already run
  `setup.sh && ci.sh` in the sandbox, and the merge phase runs it a third time, so a red
  GitHub CI is expected to be rare. Task 10 measures how rare.
- The coder box is free when the PR is queued, not when it merges. The saving per issue is
  a hypothesis until task 10 measures it. The lane removes `decompose` (up to 30 minutes),
  `improve` (up to 45 minutes on the box), the extra review with its follow-ups, and up to
  50 minutes of CI wait.
- An issue labelled `fast-path` that is not small costs one short run (D6 and D7), not a
  wrong merge. The merge phase and the Refuter review every PR in both lanes.
