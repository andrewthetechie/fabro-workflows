# 06 · Record the invariants and the new baselines

Read `00-overview-and-contracts.md` first.

## `AGENTS.md`

1. **Deployment invariants table.** Add these rows:
   - "A Child issue with a predecessor is created `agent-held`, never `agent`, and only the
     scheduler promotes it". Otherwise: the same-repo saturation race of ADR 0011 D6. Child 2
     starts from a `main` that does not have child 1.
   - "`apply_split` finds existing children through the sub-issues API, never through issue
     search". Otherwise: GitHub search updates with a delay, so a retried split would create
     duplicate children.
   - "A Split parent is never labelled `agent`". Otherwise: `backlog` would implement the
     whole issue, and also each child.
   - In the existing "A class used by a node in `_shared/review-merge/`" row, add
     `_shared/triage/` and `.plan`.
2. **The Validating section.** Update the `IssueTriage` and `ArchReview` baselines with the
   counts from task 04. Change "Both include the 10 nodes of `_shared/triage/`" to the new
   count.
3. **The layout table.** The `docs/triage-split/` row, which exists since ADR 0015 was
   drafted: change **Not started** to **Applied <date>**.

## `docs/factory-roadmap/C4-decompose-prefetch.md`

Set **Status** to "replaced by ADR 0015 (triage Task map, as a hint only). The lease-less
prefetch run and the SHA-gated skip are rejected (ADR 0015 D2)."

## `ops/README.md`

List the labels `agent-held` and `agent-split` wherever the scheduler's labels are listed,
and add the scheduler's two new jobs, promoting held children and closing split parents, to
its description.
