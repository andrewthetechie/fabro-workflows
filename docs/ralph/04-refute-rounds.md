# 04. Refute rounds

## Outcome

`refute_prep`, `refute` and `refute_gate` exist and are wired from `hygiene` (task 06 adds
`hygiene`; until then nothing reaches `refute_prep`, interim I3). A `tasks` route appends
the Refuter's provable findings to `tasks.json` and returns to `pick`. A `pass` goes to
`standards` (task 05; until then `stop_reason`, interim I4).

## Why

ADR 0029 D3. 00: O8, O15, O20, R3, C5, C10.

## Change

Read `_shared/review-merge/review-merge.fabro` from `refute_prep` to `refute_fix_gate`, its
`refute.md.j2`, `invariants-merge.md` (the Refuter rows, #40's `validated_tree` row, the
post-deploy row), and `docs/refuter-evidence/00-overview-and-contracts.md`.

### Nodes

- `refute_prep`: C5. It is not a copy: its inputs differ (no `standards.json`,
  `spec.json` or `fix_result.json` ever existed for the Refuter to see, and it adds the delta
  and the history). Reuse the shared node's issue-and-post-deploy logic by copying those
  lines; a `//` comment names the source lines. It records the visit's tree in
  `/tmp/fabro/refute_tree` for the next visit's delta.
- `refute` (agent, `.refute`, `timeout="15m"`, `max_retries=1`). The comment names the
  model's reason (ADR 0013 D6 and its luna amendment) and its fallback row.
- `refute_gate`: C5, steps 1-4. Keep the shared gate's verdict computation and its four
  published keys byte-compatible: the scorecard reads them (`summary.py`). The evidence
  rule's `path:N` test: `grep -oE '[A-Za-z0-9_./-]+:[0-9]+'` over the evidence, keep the
  path part, and test `[ -f "$path" ]`. The command test: a backticked span.
- `hygiene`'s `io-guard` hook: C1's matcher already covers `refute`.

### Edges

```
refute_prep -> refute            [condition="outcome=succeeded"]
refute_prep -> stop_reason
refute -> refute_gate            [condition="outcome=succeeded"]
refute -> refute_gate                                             // a timed-out Refuter is `invalid`: hold, never a pass
refute_gate -> pick              [condition="context.refute_route=tasks"]
refute_gate -> stop_reason       [condition="context.refute_route=pass"]                                   // I4: task 05 → standards
refute_gate -> stop_reason       [condition="context.refute_route=hold && context.loop_exhausted=false"]   // I4
refute_gate -> stop_reason       [condition="context.refute_route=hold && context.loop_exhausted=true"]    // I5: task 06 → open_pr
refute_gate -> stop_reason
```

`refute_gate` must publish `loop_exhausted` itself (read from the file), because `pick` is not
the node that ran last.

### `prompts/refute.md.j2`

The shared text, copied, plus C5's *Earlier rounds* section, plus the inputs table's two
new rows. Keep the shared text otherwise identical, so a later fix to the shared prompt can
be compared by `diff`. It is not in R10 (the added section is too large to state as a
replacement); the `//` comment above `refute` says so.

## Acceptance

- `make check` passes, including the new sections.

## Tests

`ralph — refute_gate` (fixtures in the style of the shared gate's):

1. Every criterion `met`, no defect: `pass`, `refute_verdict` `pass`.
2. One `not_met` with evidence `src/a.py:12` and `src/a.py` present, `refute_visits` 1:
   `tasks`, one Task appended with `source: refute`, `round: 1`.
3. The same with `src/missing.py:12`: dropped, listed in `refute_dropped.json`; with no other
   finding, `pass`.
4. Evidence with only a backticked command: survives.
5. A defect whose `file` is absent: dropped.
6. `refute_visits` 3, a surviving `not_met`: `hold`, verdict `fail`, no Task appended.
7. Only `unverifiable`: `hold`, `refute_unverifiable_only` true, no Task.
8. `loop_exhausted` true, a surviving finding: `hold`, no Task, and (task 06's C7 line)
   `fix_result.json` blocks.
9. No receipt: `invalid`, `hold`.

`ralph — refute_prep`: visit 1 writes no delta and no history; visit 2 writes both, the
history line names the Task and its `status`; the post-deploy cut matches the shared node's
for the same issue body.

## Depends on

03.
