# 03. `backlog`: the fast lane

## Outcome

An issue labelled `fast-path` runs `claim → prep → route_lane → next_task → coder → autofix
→ prep_review → validate → review → integrate → next_task → open_pr_prep → open_pr →
file_remainder → pr_handoff → review_merge`. The rework ladder works as in the standard
lane. `decompose`, `improve`, `excerpts` and the extra review never run. `pr_handoff` writes
`merge_mode = queue` when the C3 conditions hold, so the merge phase from task 02 queues the
PR and the run ends. An issue without the label runs exactly as today.

## Why

ADR 0023 D1-D6, operator decisions O1-O6.

## Change

Read 00: C2, C3, C9, the `backlog` graph shape, and the verified facts about `next_task`,
`improve_gate` and `rework_router`. Read `backlog/workflow.fabro` from `claim` to
`pr_handoff`, and `docs/agents/invariants-backlog.md`.

### `route_lane` (new, routing, `timeout="1m"`)

Put the comment block above the node, not inside `script=` (R6). The script:

- reads `/tmp/fabro/issue.json`. If it cannot read the labels, it exits 1. The unconditional
  edge then goes to `rescue_brief`. Never guess a lane.
- `fast` exactly when `jq -e '[.labels[].name] | index("fast-path") != null'` succeeds.
- writes `/tmp/fabro/lane`.
- in the fast lane, writes the C2 task to `/tmp/fabro/tasks.json` with
  `jq '[{id: "fast-path", title: .title, body: (if ((.body // "") | gsub("[[:space:]]"; "")) == "" then "Resolve issue #" + (.number | tostring) + ": " + .title else .body end), files: [], covers: [], source: "fast-path"}]'`,
  and `echo 0 > /tmp/fabro/task_index`.
- prints `{"context_updates":{"lane":"fast"}}` or `{"context_updates":{"lane":"standard"}}`.

### `next_task`: reset `rebase_failed` on the done branch

Both `tasks_done=true` lines print only `tasks_done`. The `rebase_failed` key from the
previous task then survives. When it is `true`, the edge to `resolve_merge` also matches,
and today only the lexical tie-break (`extra_prep` < `resolve_merge`) picks the right edge.
Change both lines to print `{"context_updates":{"tasks_done":true,"rebase_failed":false}}`
(invariants-graph, *write every key on both branches*). The new `open_pr_prep` edge below
also depends on this.

### Edges

Replace and add (old edge first, then its replacement):

```
prep -> decompose            [condition="outcome=succeeded"]
  → prep -> route_lane       [condition="outcome=succeeded"]
route_lane -> next_task      [condition="context.lane=fast"]
route_lane -> decompose      [condition="context.lane=standard"]
route_lane -> rescue_brief

next_task -> extra_prep      [condition="context.tasks_done=true"]
  → next_task -> extra_prep  [condition="context.tasks_done=true && context.lane!=fast"]
next_task -> open_pr_prep    [condition="context.tasks_done=true && context.lane=fast"]
next_task -> coder           [condition="context.tasks_done=false && context.rebase_failed=false && context.lane=fast"]
(next_task -> resolve_merge [rebase_failed=true] and next_task -> improve stay)

resolve_merge_gate -> improve [condition="context.merge_ok=true", weight=10]
  → resolve_merge_gate -> improve [condition="context.merge_ok=true && context.lane!=fast", weight=10]
resolve_merge_gate -> coder  [condition="context.merge_ok=true && context.lane=fast", weight=10]
```

`next_task -> resolve_merge [rebase_failed=true]` already matches only when
`tasks_done=false`, now that the done branch resets the key. Write a `//` comment above the
new edges that names ADR 0023 and says which standard stage each one skips.

### `pr_handoff`: the merge mode

`pr_handoff` is `set -e` and prints no routing object. Keep both. After the auto-merge switch
block, add the C3/C9 logic:

- `MM=watch`. If `/tmp/fabro/lane` is `fast` and `/tmp/fabro/remainder_kind` is absent,
  compute the ceiling. Lines ≤ 200 and files ≤ 10 give `MM=queue`. Otherwise post the C9
  comment with `gh pr comment "$PR" --body … || true`.
- The ceiling's exclude list is a copy of the hygiene `exclude_paths` defaults. Use the
  same bracket-expression regexes, without backslashes (R6), and run them with `grep -Ev`
  over the numstat path column. A `//` comment above the node names the source list.
  Test 6 below keeps the two lists equal.
- `echo $MM > /tmp/fabro/merge_mode`. A failure to compute the diff writes `watch`, because
  `watch` is the mode that waits for CI.

### What must not change

- `claim`, `prep`, `coder`, the ladder, `open_pr_prep`, `open_pr` and `file_remainder` keep
  their scripts.
- The standard lane's routing is unchanged. Every changed edge only gains
  `&& context.lane!=fast`, and `route_lane` sets `lane` on every run.
- `[run.inputs]` stays as it is. The lane is never a run input.

## Acceptance

- `make check` passes. R1, R2, R6 and R8 are clean for `route_lane` and `pr_handoff`.
- `make check-host`: `Backlog` gains the nodes and edges of this task. Record the new
  counts in `AGENTS.md`'s baseline paragraph in this commit, together with the counts from
  task 02 if those have not been recorded yet.
- `make check` (with `ops/test-task-gates.sh`) passes the new sections.

## Tests

Add `backlog — fast lane` to `ops/test-task-gates.sh`:

1. `route_lane` with `labels: [agent-in-progress, fast-path]`: `lane=fast`, and
   `tasks.json` is one task with `source: "fast-path"`, the issue's title and body, and
   `task_index` = 0.
2. `route_lane` with no `fast-path` label: `lane=standard`, and `tasks.json` is not written.
   With `Fast-Path` or `fast-path-x`: `standard`.
3. `route_lane` with an empty body: the task body is `Resolve issue #N: <title>`.
4. `route_lane` with a missing `issue.json`: exit 1, and no routing object.
5. `next_task` with `task_index` = `tasks.json` length: prints `rebase_failed":false`.
6. **The exclude lists match.** Extract the regex list from `pr_handoff` and from
   `hygiene`'s `default.json` block, and assert they are equal.
7. `pr_handoff`, in a scratch repository with a bare `origin`:
   - lane `fast`, a 30-line change: `merge_mode` is `queue`, and the `gh` log has no comment.
   - lane `fast`, 201 lines: `watch`, and one comment containing `201 lines`.
   - lane `fast`, 30 lines plus a 5,000-line `package-lock.json`: `queue`.
   - lane `fast`, 11 one-line files: `watch`.
   - lane `fast` with `remainder_kind` present: `watch`, and no comment.
   - lane `standard`: `watch`.
8. **Edge exclusivity.** With the DOT reader in `ops/check-graph-invariants.py` and a small
   evaluator for `=`, `!=` and `&&`, enumerate `lane ∈ {fast, standard}`,
   `tasks_done ∈ {true, false}`, `rebase_failed ∈ {true, false}` and
   `merge_ok ∈ {true, false}`. Assert that at most one conditional edge out of `next_task`,
   `route_lane` and `resolve_merge_gate` matches each combination that the nodes can
   publish.

## Depends on

02. The phase must accept `merge_mode` before `pr_handoff` writes it.
