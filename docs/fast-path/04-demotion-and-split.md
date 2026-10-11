# 04. Demote a fast task that the ladder cannot land; keep `fast-path` off split children

## Outcome

In the fast lane, rework round 6 goes to `demote_lane` instead of the rescue gate. The issue
loses `fast-path`, goes back to `agent`, and gets a comment with the last findings. The run
ends with no PR, and the scheduler dispatches the issue again in the standard lane.
Separately, `apply_split` never copies `fast-path` to a child issue.

## Why

ADR 0023 D7 (drafted, P3) and ADR 0023 D1. A standard task that exhausts the ladder waits
up to 4 hours at `human_rescue` and holds a coder box. For a fast-path issue, that result
means that the label was wrong. The standard lane, with `decompose` and `improve`, is the
better next step, and it needs no human.

## Change

Read 00: C1, C2, the `backlog` graph shape, and the verified fact about `rework_router`.
Read `drop_followup`, `rework_router`, `mark_stuck` and `rescue_brief` in
`backlog/workflow.fabro`, and `apply_split` in `_shared/triage/triage.fabro`.

### `demote_lane` (new, no routing schema, `timeout="2m"`)

The order of the label calls is load-bearing. Write a `//` comment that says why.

1. `N` from `/tmp/fabro/issue.json`, falling back to `/tmp/fabro/issue_number`, the same
   way `mark_stuck` does. If `N` is not a positive integer, exit 1.
2. `gh issue edit $N --remove-label fast-path`. **If this fails, exit 1.** Then confirm
   with `gh issue view $N --json labels` that `fast-path` is gone. If it is still there,
   exit 1. Only then is it safe to requeue: an issue that kept `fast-path` and got `agent`
   back would run the fast lane again and demote again, without end.
3. Build the comment: the fixed sentence
   `The fast path could not land this issue: the task failed validate or review at every rework tier. fast-path was removed and the issue is back in the queue for the standard lane, which decomposes it first.`,
   then a fenced block with the first 20 non-empty lines of `/tmp/fabro/feedback/rework.md`,
   with control characters removed (as `rescue_brief` does).
4. `gh issue edit $N --add-label agent --remove-label agent-in-progress || true`, then
   `gh issue comment $N --body-file … || true`. `agent` is added in the same call that
   removes the receipt, so an interruption cannot leave the issue with neither label
   (`dispatch.py` explains this order).
5. `echo demoted`.

The scheduler does not dispatch the issue again while this run's lease exists, because
`choose_next` skips an item whose `(repo, issue)` already holds a lease. The standard run
starts after this run ends.

### Edges

```
rework_router -> demote_lane  [condition="context.round=6 && context.lane=fast"]
demote_lane   -> exit         [condition="outcome=succeeded"]
demote_lane   -> rescue_brief
```

A fast task is never `task_exempt` (its `source` is `fast-path`), so this edge and the
`drop_followup` edge cannot both match. Rounds above 6 happen only after a human's guidance
at the gate, and they keep the existing route to `rescue_brief`.

### `rescue_brief`

No change to its logic. If a fast-lane run reaches the gate for another reason (a failed
agent stage), the brief is still correct. `[X] Abandon` keeps `fast-path` on the issue,
which is the operator's choice to undo.

### `apply_split`

Add `"fast-path"` to the excluded-label array in the `PL=` line, next to the queue-state
labels. Add it in one place only, and change nothing else in that line.

## Acceptance

- `make check` passes.
- `make check-host`: `Backlog` gains 1 node and 3 edges. Update `AGENTS.md`'s baseline
  paragraph in this commit.

## Tests

Extend `backlog — fast lane` in `ops/test-task-gates.sh`, with a `gh` stub that logs its calls
and returns a scripted label list:

1. A successful demotion: the log shows `--remove-label fast-path`, then the
   `gh issue view`, then `--add-label agent --remove-label agent-in-progress`, then the
   comment, in that order. The comment holds the fixed sentence and the findings.
2. `--remove-label fast-path` fails: exit 1, and no `--add-label agent` call.
3. The view still lists `fast-path` after the removal: exit 1, and no `--add-label agent`.
4. No issue number anywhere: exit 1, and no `gh` call.
5. Edge exclusivity (the evaluator from task 03): for `round ∈ {5, 6, 7}`,
   `lane ∈ {fast, standard}` and `task_exempt ∈ {true, false}`, at most one conditional
   edge out of `rework_router` matches.

Extend the existing `apply_split` section: a parent with `fast-path` produces children
whose `--label` arguments do not include it.

## Depends on

03.
