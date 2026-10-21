# 05. The pre-PR review

## Outcome

After the Refuter passes or holds, `standards` and `spec` review the branch. Their `error`
findings go back to the loop while Refute rounds remain; otherwise `review_gate` writes the
merge inputs and routes to `open_pr` (task 06; until then `stop_reason`, interim I6).

## Why

00: O9, O15, O20, R4, R5, C6, C10.

## Change

Read the shared `standards`, `spec`, `review_fix`, `fix_gate` and `prep_review` nodes, their
prompts, the `standards`, `spec` and `fix_result` schemas, and `merge_gate` checks 7-10.

### Nodes

- `standards` (`.review-standards`, `timeout="25m"`, `max_retries=1`) and `spec`
  (`.review-spec`, `timeout="15m"`, `max_retries=1`): the shared timeouts. The comment above
  them says the timeouts were measured on glm-5.3-flash and glm-5.3, and that `kimi-k3` is
  unmeasured on these prompts (invariants-graph, *Timeouts*): re-derive from the shakedown.
- Their inputs: whatever the shared `prep_review` writes for them (the diff, the changed
  files, the issue). If `ralph` lacks a node that writes one of them, `review_prep` (new,
  command) writes it; copy the shared lines and name the source.
- `review_gate`: C6. Receipt checks on both results. Writes `fix_result.json` in the shared
  schema's shape (validate it against `_io/schemas/fix_result.schema.json` in the test, not in
  the gate). Publishes `review_route` (`tasks`, `pass`), and `merge_block_reason` when
  `fix_outcome` is not mergeable.

### Edges

```
refute_gate -> review_prep        [condition="context.refute_route=pass"]                                    // replaces I4
refute_gate -> review_prep        [condition="context.refute_route=hold && context.loop_exhausted=false"]    // replaces I4
review_prep -> standards          [condition="outcome=succeeded"]
review_prep -> stop_reason
standards -> spec                 [condition="outcome=succeeded"]
standards -> stop_reason
spec -> review_gate               [condition="outcome=succeeded"]
spec -> stop_reason
review_gate -> pick               [condition="context.review_route=tasks"]
review_gate -> stop_reason        [condition="context.review_route=pass"]                                     // I6: task 06 → open_pr
review_gate -> stop_reason
```

A reviewer that times out stops the run at `stop_reason` (no PR yet). That is stricter than
the shared phase, where a reviewer timeout reaches `mark_needs_human` with a PR open; it is
what R1 implies, and the shakedown shows whether it is too strict.

### Prompts

`ralph/prompts/standards.md.j2` and `spec.md.j2`: the shared texts, changed only where they
mention `review_fix` (nobody fixes in this stage: findings become Tasks for the coder), and
in `spec`, the `risk` field with `review_fix.md.j2`'s rubric, copied. Each is an R10 row of
kind `prompt` if the changes can be stated as replacements; otherwise say so in the comment.

## Acceptance

- `make check` passes, including the new sections.

## Tests

`ralph — review_gate`:

1. Both `approve`, `risk` 2: `pass`; `fix_result.json` mergeable, `not_fixed` empty, valid
   against the `fix_result` schema.
2. One `error` finding, `refute_visits` 1, `refute_route` `pass`: `tasks`, one Task with
   `source: review`.
3. The same with `refute_visits` 3: `pass` route, `fix_result.json` not mergeable,
   `not_fixed` lists it, `merge_block_reason` set.
4. Only `warning` findings: `pass`, mergeable, the warnings in the report file.
5. `refute_route` `hold` and one `error`: no Task (the round is spent on the Refuter's hold);
   not mergeable.
6. `needs_human_review` on `spec`: not mergeable, reason names it.
7. `risk` 4: mergeable `fix_outcome` but `merge_gate` check 8 will block (assert the value is
   written; task 07's `merge_gate` fixtures cover the block).
8. A `review`-sourced Task `passed` in an earlier round: its finding id is in
   `fixes_applied`.

## Depends on

04.
