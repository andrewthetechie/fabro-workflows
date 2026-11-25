# Task 09: the issue-level order

Read `00-overview-and-contracts.md` first (E14, P6, C7, *Edges*, second block). **Invoke
`/fabro-workflow`.** Read `docs/agents/invariants-ralph.md` *The pre-PR review* and the comments above
`refute_prep`, `refute_gate`, `review_prep` and `review_gate` in `ralph`'s graph.

## Steps

1. Replace the edges between `local_review_prep`, `local_review_gate`, `review_prep`, `standards`, `spec`,
   `refute_prep`, `refute`, `refute_gate` and `review_gate` by the second block of *Edges*.
2. `review_prep`, `refute_gate` and `open_pr`: C7's edits, each as one recorded replacement in
   `R10_V2_COPIES`. For `review_prep` the replacement's `old` is the line `B=main` at the top (occurs once).
   For `refute_gate` it is the `reason: \"not passed when the loop ran out\"` text. For `open_pr` it is
   the `(.status // \"open\") + \")\"` part of the *Unmet criteria* line.
3. Comments above these four nodes: two lines each, saying what v2 changed and why (the order, P6).
4. `check-host.sh` baseline: `68 nodes, 176 edges`.

## Tests (`test_ralph_v2.py`)

- The edge list equals the block in 00, edge for edge, and no edge goes from `refute_gate` to `review_prep`.
- `review_prep`: exhausted with a 3 MB diff exits 0 with both keys `false`; the extra pass re-runs one axis;
  a normal visit runs both.
- In real order with the real scripts: review errors and Refuter findings in one round become Tasks
  together; a Refuter pass with review errors in round 1 and in round 3 (the extra pass: the Refuter is
  skipped and `refute_route` is still `pass`); a Refuter `hold` with rounds spent opens the PR through
  `review_gate` with `needs_human`; an exhausted run reaches `open_pr` with no hosted reviewer and its
  `not_fixed` reasons name each `block_reason`; a blocked Task is listed under *Unmet criteria* with its
  reason.
- `fix_result.json` written by `refute_gate` on an exhausted run survives to `open_pr`.
- `prfacts.report_has_items` still finds an item in a report rendered from that file (run `render.jq`
  as `test_merge_report.py` does).

## Done when

- `make check` passes. Counts are **68/176**.
- Commit: `feat(ralph-v2): hosted reviews first, the Refuter last, one combined fix (ralph-v2 09)`.
