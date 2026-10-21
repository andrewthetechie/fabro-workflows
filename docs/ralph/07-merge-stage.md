# 07. The merge stage and the drift gate

## Outcome

After `pr_handoff`, the run waits for CI and merges its own PR. Red CI and a base conflict go
back to the coder, at most twice. A PR that passed every gate but ran out of deadline gets
`merge-on-green`. `ops/check-graph-invariants.py` R10 fails when any `ralph` copy drifts from
its original.

## Why

ADR 0029 D4. 00: O10-O15, R6, C8.

## Change

Read all of `_shared/review-merge/review-merge.fabro` from `deliver` to `done`, every row of
`invariants-merge.md`, `docs/fast-path/00-overview-and-contracts.md` (queue mode, the
approval marker), `docs/flakeradar/00-overview-and-contracts.md` (F8-F10), and
`ops/check-graph-invariants.py` (the DOT reader, R7, `R7_EXCEPTIONS`).

### R10 first

Add `R10_COPIES` and the rule (C8). Fill the table with every copy tasks 02-06 made, using
the `// R10:` comments they left, and delete those comments. Then add this task's copies.
Run `make check`: every row must pass before you write the merge nodes' edges.

A row's `replacements` may be empty. A replacement's `old` must be specific enough to occur
once; prefer a whole line. Keep the list short: if a node needs more than about five
replacements, it is not a copy (C8), so say so in a comment and leave it out of the table.

### Nodes

Copies of the shared nodes: `deliver`, `merge_gate`, `watch_checks`, `merge`, `remerge_base`,
`resolve_base`, `resolve_base_gate`, `report_merged`, `report_blocked`, `report_queued`,
`mark_needs_human`. Expected replacements: paths or file names that the shared phase gets
from a node `ralph` does not have (`rm_setup`'s `review_entry_tree`: `ralph` writes it in
`pr_handoff`); `report_queued`'s queue-mode guard (it may run in `watch` mode in `ralph`, O12).
Read each node's inputs and list every file it reads that no `ralph` node writes. Each one
needs a writer in `ralph` or a replacement; none may be left to "absent means fine".

New:

- `ci_task`: C8. The log excerpt: `watch_checks` already saves the failing check names and
  logs for `ci_fix`'s prompt; read the same files.
- `pr_handoff`'s replacement writes `post` to `/tmp/fabro/phase`, `watch` to
  `merge_mode`, and `review_entry_tree`.

### Edges

Start from the shared phase's edges for the copied nodes, and change only these:

```
pr_handoff -> merge_gate              [condition="outcome=succeeded"]          // replaces I7
pr_handoff -> stop_reason
watch_checks -> ci_task               [the shared phase's condition for ci_fix_t1]
ci_task -> pick                       [condition="context.ci_route=task"]
ci_task -> report_blocked             [condition="context.ci_route=stop"]
ci_task -> stop_reason
pick -> hygiene                       [condition="context.loop=done && context.phase=post"]
pick -> report_blocked                [condition="context.loop=exhausted && context.phase=post"]
hygiene -> deliver                    [condition="outcome=succeeded && context.phase=post"]
resolve_base_gate -> final_check      [the shared phase's condition for its route to validate]
final_check -> hygiene                [condition="outcome=succeeded && context.final_route=pr && context.phase=post"]
final_check -> mark_needs_human       [condition="outcome=succeeded && context.final_route=needs_human"]
stop_reason -> mark_needs_human       [condition="context.stop_phase=post"]
watch_checks -> report_queued         [the deadline and pending conditions, C8]
report_merged / report_blocked / report_queued / mark_needs_human -> exit     // task 08 → learn_prep
```

Every edge the shared phase sends to `ci_fix_t1`, `ci_fix_t2`, `ci_fix_gate`, `validate`,
`refute_prep` or `refute` has no target in `ralph`; each must be mapped by the list above.
Write the mapping as a table in the `//` comment above `ci_task`.

`final_check`'s `pr` edge for phase `pre` (task 06) must gain `&& context.phase=pre`, so the
two `pr` edges are exclusive.

`resolve_base` adds 1 to `post_iterations` before it runs (R6): put that line in
`remerge_base`'s conflict branch as a replacement, or in a one-line command node before
`resolve_base`.

### Hooks

`workflow.toml` gets C1's report hooks, anchored `^report_merged$` and so on. R7 must see a
`checkpoint_saved` hook for each of `report_merged`, `report_blocked`, `mark_needs_human` in
`ralph`; extend R7's importer list to include `ralph`'s own nodes if it only scans imports.

## Acceptance

- `make check` passes: R7 and R10 clean, every copied node's fixtures in
  `ops/test-task-gates.sh` that exist for the shared node run against the `ralph` copy too
  (parametrize the existing sections by graph where they extract a script).
- Edge exclusivity holds for `watch_checks`, `ci_task`, `pick`, `hygiene`, `final_check`,
  `stop_reason`.

## Tests

`R10`: a fixture graph pair where the copy equals the original (pass), differs by one
character (fail, naming the node), uses a replacement whose `old` occurs twice (fail), and
uses one that does not occur (fail).

`ralph — ci_task`: `post_iterations` 0: one `ci` Task appended, `phase=post`;
`post_iterations` 2: `stop`, both reason files.

`ralph — merge stage` (real git, a bare `origin`, `gh` stubbed as the shared fixtures do):
red CI, then an Iteration, `hygiene`, `deliver` pushes the new head; a tamper counter after
a CI-fix Iteration blocks at `merge_gate`; a Known-flaky red takes the second rerun and
never reaches `ci_task`.

## Depends on

06.
