# `ralph-v2`, a box-first workflow with per-Task planning and review, is trialled beside `ralph`

**Status:** proposed (2026-11-23, revised 2026-11-24, issue #87). Accepted or rejected on the
trial's result (`docs/plans/ralph-v2/14-read-the-result.md`). Every decision is the operator's, from
the planning session of 2026-11-23. Contracts: `docs/plans/ralph-v2/00-overview-and-contracts.md`.

`ralph` (ADR 0029) reviews only when every Task has passed. Defects found there cost a round trip
of about 54 minutes, 85 times in 133 runs, and the fixes themselves draw later findings twice as
often as the planned work (21% against 12%). Findings arrive one at a time. Its whole-branch box
lenses show no clear downstream effect.

## D1. A parallel package, by issue parity

`ralph-v2` is a separate package, `.fabro/workflows/ralph-v2/`. Even issue numbers run it, odd ones
run `ralph`, and `fast-path` stays on `backlog`. It is built complete. Rejected: an arm inside
`ralph`'s graph (every v1 invariant becomes conditional on the arm), and a shadow period.

## D2. The box is first at everything; hosted is the fallback and the final check

Planning, coding and Task-level review run on the leased box. Hosted models fall in on a server
error, on a failing Task (a ladder), or as the cross-family check on the whole branch. `kimi-k3` is
removed from both arms. Task-level review uses no hosted model unless the box cannot produce a result.

## D3. A Task passes only when its box review is clean

A per-Task plan (dossier refresh, goals, edge cases, bug class) and, after every mechanical pass, a
box refuter and a box standards/spec review. Errors send the Task back to the box; a Task gets four
reviews and is then blocked. A rejected review never climbs the ladder. The coder may dispute a
finding.

The risk is named: a same-model reviewer shares the coder's blind spots, which is ADR 0013 D6's
objection. It is acceptable because the whole-branch review and the luna Refuter remain, and because
the experiment can say no.

## D4. The issue-level round ends with the Refuter

`standards`, `spec`, then `refute`, in one round with one combined fix. Order is the only change to
v1's round. Both arms review on `glm-5.3`, so the quality measure compares like with like.

## D5. Amendments

- **ADR 0013 D6**, for `ralph-v2` only: the Refuter's first fallback is `glm-5.3-flash`, the
  reviewers' family, then qwen. Independence from the coder is the part that matters, and qwen is
  the box's family.
- **ADR 0030 D1**: the box-plan trial closes as "the box wins". `plan` runs on the box in `ralph`,
  `backlog` and `ralph-v2`.
- The hygiene rulings this ADR first proposed shipped on their own as ADR 0013's #92 amendment
  (2026-11-24), to every package.

## D6. A run uploads its own package

A workflow version holds the dispatched package and `_shared/`, not the whole `.fabro/` tree. The
whole tree was 1,993,895 bytes on 2026-11-24 against fabro's 2 MiB limit, so a second `ralph`-sized
package would have stopped every dispatch.

## D7. The measure, and what decides

Two co-primary measures: zero-touch merges per run, and blocking findings in the first issue-level
hosted round. Guardrail: lease-hours per merge within +50%. Four weeks, or 150 runs an arm,
whichever is later. Throughput is expected to fall and is accepted.
