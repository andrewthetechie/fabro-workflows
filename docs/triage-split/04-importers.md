# 04 · Importers count `split`

Read `00-overview-and-contracts.md` first. The contract for this task is C4.

## `arch-review/workflow.fabro`

- `build_queue`: the `tally.json` seed gains `\"split\":0`.
- `next_issue`: add `split` to the `case` list. Without it, a split is counted as
  `released`.
- `summarize`: read `SP` from the tally. Count it in `TR`, and print it as `$SP split` after
  `agent` (`… 3 agent, 1 split, 2 needs-info …`).
- `loop_restart_signature_limit`: keep **20**, and confirm it with this reasoning. The
  breaker counts each failure **signature** run-wide, and each gate prints its own fixed
  line, so `plan_gate`'s failures are counted apart from `triage_gate`'s and
  `improve_gate`'s. With at most 18 issues and one repair each, a single signature
  reaches 18 at most, which is below 20. `plan_gate`'s invalid-file line must be
  **different from** the other gates' lines. Give it its own text (`plan.json missing or
  invalid; …`). This is the AGENTS.md invariant on imported phases run in a loop.
- `stall_timeout`: `plan` is 30m, the same as the other agents in the phase. `60m` still
  exceeds every node, so no change is needed. Confirm it.

## `issue-triage/workflow.fabro`

No routing reads the outcome. Only the stylesheet changes, and task 02 did that. Confirm
that `triage_phase -> exit` is still the only edge.

## Tests

In the `arch-review` section of `ops/test-task-gates.sh`, if there is one, or else in a new
one: `next_issue` with `triage_outcome=split` increments `split`, and `summarize` prints
`1 split`.

## Acceptance

`fabro validate`: record the new `ArchReview` and `IssueTriage` node and edge counts. Task
06 writes them into AGENTS.md as the new baselines. The routing checker is clean.
