# Triage drafts a Task map, and splits an issue that is too large into ordered Child issues

**Status:** accepted (2026-09-26), applied 2026-09-27 (`docs/triage-split/` tasks 01–07; the map path is verified live, and the first live split and the task 07 measurement are pending). Implementation plan: `docs/triage-split/`. Roadmap item
`docs/factory-roadmap/C4-decompose-prefetch.md`, which this replaces in part.

The shared triage phase (`_shared/triage/`, imported by `issue-triage` and `arch-review`)
decides whether an issue is ready and promotes it to `agent`. After that, `backlog`
decomposes the issue on the lease window, and it learns only then whether the issue is
too large. We add a `plan` stage to the triage phase. It drafts a **Task map** for every
issue that triage finds ready. When the map is larger than one run should carry, triage
**splits** the issue into at most three **Child issues**, ordered by their dependencies.
`backlog`'s decomposer reads the map as a hint, and it always decomposes for itself.

## Evidence (2026-09-26)

- `decompose` is not a large share of lease time. In the 9 measured `backlog` runs it had 8
  visits, a median of 4.3 min, and 0.57 h in total, against 12.2 h for `coder` (the ADR 0014
  measurement). C4 said not to prefetch until this share is material. It is not.
- Run size is the larger lever. ADR 0011 names oversized issues as a cause of the 4-of-33
  auto-merge rate. The size problem shows up inside tasks: the `covers > 3` warning,
  `improve` splits, and coder timeouts. It does not show up as runs that spend the 8-task
  budget: no Remainder issue was filed in the 75 PRs since ADR 0011 D6 was applied.

So the value is in smaller issues (D3–D5). A head start for `decompose` is a secondary
gain (D2).

## D1. A separate `plan` agent drafts the map, only when triage says `ready`

`triage_gate` → (`ready`) → `plan` → `plan_gate`. Then `plan_gate` routes to `apply_ready`
(with the map), to `apply_split`, or to `post_questions` (D5). `plan` has its own contract
file (`/tmp/fabro/plan.json`), its own timeout and its own attempt counter. The triage
prompt stays the same size. A bad map costs one map retry, not a new triage. `plan` uses
the `high-reasoning` class, the same as the phase's other agents, and has a rule in both
importing stylesheets.

**`plan` fails open.** If `plan` fails, or writes an invalid map twice, the issue is
promoted the way it is today, with no map. A broken map never costs a triage verdict.

## D2. The map is a visible skeleton in the issue body, and `decompose` treats it as a hint only

`apply_ready` writes a `## Proposed task map` section into the issue body. It replaces any
earlier one, the same way `## Decisions made during triage` is replaced. The section
records the `main` SHA and the date that the map was drafted against, and one entry per
task: `id`, title, a one-line intent, `files`, `covers`, and order.

- **A skeleton, not full task drafts.** The map is never used verbatim, so full bodies
  would only make the issue longer.
- **In the body, not a comment.** `backlog`'s `claim` fetches the body and no comments. A
  human can read and correct the map before the issue is worked.
- **`decompose` always runs** (operator decision). It starts from the map, checks every
  file against the checkout, keeps an entry's `id` when it keeps that slice (so the dedupe
  keys stay stable), and may merge, split or drop entries. When the map and the checkout
  disagree, the checkout wins. Nothing skips `decompose`, so there is no SHA drift check
  and no hidden machine-readable copy. The C4 designs "Lever A" and "SHA-gated skip" are
  rejected.

## D3. Split when the map has more than 4 tasks, into at most 3 children of at most 4 tasks

The rule is mechanical and lives in `plan_gate`. The prompt only explains it:

- 1–4 tasks: no split.
- 5–12 tasks: split into 2 or 3 children. Each child has at most 4 tasks, each task is in
  exactly one child, and each child keeps the order of its tasks.
- More than 12 tasks: D5.

Four is half of the run's 8-task budget (ADR 0011 D6). Children **never split again**:
they are created ready, with their own Task map, and they skip triage. Split is allowed
only on `ready`. A `mixed` classification ("recommend splitting") now becomes a split when
the map is large enough. Every kind of issue can be split, including human-written ones.
The split is written into the parent's `## Decisions made during triage` so that a human can
reverse it. Children inherit the parent's labels. `architecture` must carry over, or an
architecture child would auto-merge (ADR 0012, the `architecture` invariant).

## D4. Ordered children are held until their predecessor lands

Each child names at most one **earlier** child as its predecessor (`after`). A child with
no predecessor gets `agent` at once. A child with one gets `agent-held` and a first-line
marker `<!-- fabro:split-child parent=<P> after=<N> -->` (`after=0` for none). It is a
**Held issue**. The scheduler already promotes Remainder issues. It gains a second
promoter. When issue `N` is closed as `completed`, it adds `priority`, then `agent`, then
removes `agent-held`. When `N` is closed as `not_planned`, it adds `agent-stuck`. This is
the ADR 0011 D6 promotion order, for the same reason: `agent` goes on before the hold label
comes off.

Without the hold, the scheduler's same-repo saturation would start child 2 on the other
box, from a `main` that does not have child 1. That is the race that D6 closed for
Remainder issues. Promoted children get `priority`, so a split feature drains in one piece.
The first child queues by issue number, so a split never jumps its parent's place.

## D5. More than 12 tasks is a scoping question for a human

`plan_gate` rewrites the verdict to `needs_info`, with one question: "This is N tasks; which
part should come first, or should the scope be cut?". It adds the map to `triage.md` as
evidence, and it routes to `post_questions`. Splitting such an issue into three oversized
children and letting the Remainder issue absorb the rest is rejected: an issue that large
is a product decision.

## D6. The parent tracks its children through GitHub sub-issues, and the scheduler closes it

`apply_split` creates each child with `gh issue create`, in order, and links it to the
parent through the sub-issues API. It labels the parent `agent-split` (never `agent`), writes
the map and a `## Split into` checklist into the parent's body, and publishes the new phase
outcome `split`. Before it creates anything, it lists the parent's existing sub-issues and
reuses them. So a retried split cannot create duplicates. The sub-issues API is strongly
consistent, and issue search is not.

The scheduler closes a parent as `completed` when every sub-issue is closed as
`completed`. If any child closes `not_planned`, the parent gets `agent-stuck` and stays
open for a human.

## Consequences

- `arch-review` spends one more agent session on each ready issue, inside its unchanged
  6-hour cap (operator decision). It triages fewer issues in one review, and the rest wait
  for the next review.
- The phase's exit contract gains `split`. Every importer that counts outcomes
  (`arch-review`'s `next_issue` and `summarize`) must accept it.
- The scheduler deploy must come before the graph change. If it does not, held children
  wait with nothing to promote them. A scheduler rebuild dates the draft-14 shakedown
  window again.
- Issues that are already `agent` get no map. There is no backfill.
