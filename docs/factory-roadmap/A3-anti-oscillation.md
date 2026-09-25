# A3 · Anti-oscillation: a decision ledger and three deterministic guards

**Status:** proposed. **Axis:** repeatability, quality. **Effort:** M.
**Feasibility:** high (none of it depends on model behaviour). **Depends on:** A2 (the
`proposed` state). Uses C1's task `kind` field if it exists, but does not require it.

## Problem

Nothing stops one improvement loop from undoing another.

- `arch-review` removes duplicate candidates only by an **LLM-chosen slug**
  (`file_issues`, `arch-review/workflow.fabro:87`, contract C2 in
  `docs/architecture-review/00-overview-and-contracts.md`). The reverse of a merged refactor
  gets a new slug, so it can be filed, triaged and implemented.
- Four producers push on the same files: `arch-review` refactors, `extra_decompose` follow-up
  tasks, reviewer "should" findings acted on by `review_fix`, and human issues. None of them
  sees why the current structure was chosen, unless it happens to be in `CONTEXT.md` or an ADR
  that the prompt reads.
- A declined proposal leaves no durable trace except a closed issue whose slug the next
  review may not reuse.

The failure is a token-and-box-hour loop that looks like progress on the page.

## Design

Four layers, cheapest first. Only layer 1 relies on an LLM reading text. Layers 2–4 are code.

### Layer 1: decisions ride the PR that makes them

Every factory PR that changes structure must add or amend one ADR in the target repo's
`docs/adr/` (all four target repos keep ADRs, 19–35 each). "Changes structure" means an
Architecture issue, or a task whose `kind` is `refactor` once C1 adds the field. The coder or
`review_fix` writes it. A new `merge_gate` check verifies that
`git diff --name-only origin/main...HEAD` contains a path under `docs/adr/`, and blocks with a
clear reason if not. The ADR is reviewed and merged with the code. `scan.md.j2` and
`triage.md.j2` already treat ADRs as "basis" (ADR 0012 D4), so the next scan meets the
decision in the place it already looks.

### Layer 2: the inverse-diff guard (deterministic)

A command node in `review_merge`, after `validate`, before `merge_gate` (next to A4):

1. List factory-authored commits merged to `main` in the last 60 days, identified by the
   `agent-authored` PR label via `gh pr list --state merged --label agent-authored --json
   mergeCommit,number,mergedAt`.
2. For each file this branch changes, compare its **added** lines against the lines those
   commits **removed**, and its removed lines against their added lines, by normalised line
   hash (trim whitespace, drop blank lines).
3. If more than a threshold (start at 40%) of a file's changed lines invert an earlier
   factory commit, write `oscillation.json` naming the earlier PR, add the
   `oscillation-suspect` label, and let `merge_gate` block with the reason *"This PR reverses
   #N (merged <date>). A human decides which direction is right."*

It is exact, it runs in seconds, and it does not care what anything was called.

### Layer 3: file cool-down for factory proposals

In `arch-review` `file_issues`, skip a candidate when more than half of its `files` were
changed by a factory-merged PR labelled `architecture` in the last 30 days. Log
`skip <slug>: cool-down, files changed by #N`. Human-filed issues are never cooled down.

### Layer 4: rejected proposals stay rejected

When a `proposed` or `architecture` issue is closed as "not planned", or a factory PR is
closed unmerged, record `{slug, files, reason, closed_at}`. Recommended store: the target
repo's `docs/adr/rejected-proposals.md`, committed out-of-band by the scheduler the same way
B7 writes factory notes. The `history` node in `arch-review` passes it to the scan with
`existing.json`, and `file_issues` skips any candidate whose `files` overlap a rejected entry
by more than half, whatever its slug.

## Verification

- `ops/test-task-gates.sh` with **real git** (the `open_pr_prep` section is the pattern):
  build a repo, merge a factory commit, then stage a branch that reverts 60% of it and one
  that touches the same file without inverting it. Only the first trips.
- `arch-review` fixtures: a candidate inside the cool-down is skipped, and a candidate whose
  files overlap a rejected entry is skipped.

## Open questions

1. The threshold (40%) and window (60 days) are start values. B1's revert-rate column shows
   whether they are too loose.
2. Layer 1 could make small refactors expensive. Suggest scoping it to Architecture issues
   first and widening it once C1's `kind` exists.
