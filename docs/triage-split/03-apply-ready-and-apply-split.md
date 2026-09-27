# 03 · `apply_ready` writes the map, and `apply_split` creates the children

Read `00-overview-and-contracts.md` first. The contracts for this task are C2, C3 and C4.
Invoke `/fabro-workflow` first.

## `apply_ready` (changed)

After the existing Decisions block, and before `gh issue edit --body-file`, do this when
`/tmp/fabro/plan_ok` exists: drop any existing `## Proposed task map` section with the same
`awk` as the Decisions section, and append `/tmp/fabro/task_map.md`. Build the body once,
from Decisions plus map, and edit it once. Without `plan_ok`, the node behaves exactly as
it does today.

## `apply_split` (new, `shape=parallelogram`, `output_schema="routing"`, `set -e`)

1. **Idempotency first.** Read `gh api repos/{owner}/{repo}/issues/$N/sub_issues`. For each
   entry in `split_children.json`, in order, reuse an existing sub-issue whose title is
   equal to the child's title. Create only the ones that are missing. Record
   `index → number` in `/tmp/fabro/split_numbers.json`.
2. **Create each missing child, in order**, with `gh issue create --title "$T"
   --body-file <file> <labels>`. The body is C3's layout. `after=<N>` is the **issue number**
   of the predecessor child, which is always already known because children are created in
   order. The labels are C3's, and every label is created first (the phase's existing
   rule). Take the issue's `id` from `gh api repos/{owner}/{repo}/issues/$C --jq .id` and
   link it with `gh api -X POST repos/{owner}/{repo}/issues/$N/sub_issues -F
   sub_issue_id=$ID`. A 422 "already a sub-issue" is success.
3. **The parent.** Write the new body: the Decisions section, which gains one line,
   `- **Split this issue?** Into #a, #b, #c (basis: task map of K tasks; ADR 0015 D3)`,
   then the map section, then a `## Split into` checklist `- [ ] #a <title>`. Post the
   triage report comment, as `apply_ready` does. Run `gh issue edit --title "$T"
   --add-label agent-split <triage labels> --remove-label` for the triage labels. Never add
   `agent`.
4. Write `split` to `/tmp/fabro/triage_outcome`, and print
   `{context_updates:{triage_outcome:"split"}}`.

Edges: `apply_split -> done [condition="outcome=succeeded"]` and `apply_split -> release`.

`apply_split` creates its two new labels itself, like the phase's other
`gh label create … || true` calls: `agent-held` (`C5DEF5`) and `agent-split` (`BFD4F2`).
There is no provisioning step.

## `done` and the exit contract

Add `split` to `done`'s `case`, and to the exit-contract comment at the top of the file.

## Tests (triage section of `ops/test-task-gates.sh`)

Use the `gh` stub that the triage section already has. Extend it to answer `sub_issues` and
`issue create`, and to log every call.

- `apply_ready` with `plan_ok`: the body has one map section, and run twice it still has
  one. Without `plan_ok`, the `gh` calls are the same as before this change, compared
  line for line.
- `apply_split` with 3 children (`after`: null, 0, 1):
  - it creates them in order;
  - child 1 is `agent`, children 2 and 3 are `agent-held`;
  - the markers carry the real predecessor numbers;
  - there are 3 sub-issue POSTs;
  - the parent gets `agent-split` and never `agent`;
  - `architecture` is inherited by every child when the parent has it;
  - the outcome is `split`.
- Re-run with the stub reporting 2 existing sub-issues: exactly 1 `issue create`.
- `done` publishes `split`.

## Acceptance

`fabro validate` is clean for both importers, now 3 nodes above the pre-series count. The
routing checker is clean. The gate suite passes.
