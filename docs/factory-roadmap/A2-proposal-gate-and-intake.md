# A2 · A human approves a proposal before it is implemented, and human-filed issues enter fast

**Status:** proposed. **Axis:** quality, throughput. **Effort:** S (labels), S–M (intake).
**Feasibility:** high. **Depends on:** nothing. **Amends:** ADR 0012 D8.

## Problem

### Proposals are implemented before any human sees them

The operator's requirement is that features and refactors the factory proposes itself wait
for a human **before implementation**. ADR 0012 D8 chose the opposite. An Architecture issue
goes to `agent` like any other triaged issue, and the human step is the merge click on the
finished PR. D8 recorded the rejected alternative: *"We rejected a label other than `agent`
that a human must promote. That label would stop the issues before the backlog, and the goal
is issues in the backlog."*

The cost shows up in the queue. On 2026-09-25 both coder boxes were working `jelly-swipe`
Architecture issues #398 and #399, filed by `arch-review` at 04:00, triaged to `agent` with
no human, and bumped to the front. Each is a 4–8 box-hour run whose PR a human must still
review, because `merge_gate` check 2b blocks it. A proposal the operator would decline costs
those box-hours plus review time. Declining it as an issue costs one click.

### Human-filed issues wait days for triage

The `issue-triage` automations are provisioned **disabled** (`ops/provision-server-state.sh`,
the `issue-triage-*` rows). A `needs-triage` issue is therefore triaged only when that repo's
`arch-review` next runs (twice a week, and then only as one of at most 10 non-review issues,
ADR 0012 D5). The operator's intended intake is "file an issue with pi or Claude, and the
loop picks it up," which today means a wait of up to 3.5 days unless the issue is filed with
`agent` directly.

## Design

### Part 1: the `proposed` label (S)

- In `_shared/triage/triage.fabro`, node `apply_ready`: when `.labels` of `issue.json`
  contains `architecture`, or any label in a small **proposal set** (start with
  `architecture` and `factory-proposal`), add `proposed` instead of `agent`. Create the label
  first (the idempotent `gh label create` pattern). Publish the outcome as a new word,
  `proposed`, and extend contract C1's word list, `done`'s `case`, and `arch-review`'s
  `tally.json` and summary line.
- The operator promotes by swapping `proposed` for `agent`. The scheduler already queues
  anything labelled `agent`, so nothing else changes.
- **Keep D8's merge block** (`merge_gate` check 2b) as defence in depth. An approved proposal
  is still an LLM refactor, and its PR still waits for a merge click.
- Bugs the factory files itself (A1's red-main issue, B6's scout findings) are **not** in the
  proposal set. A failing test needs no product judgment.

### Part 2: `requires-human-review` (S)

Rev 2's P0-2, still open. A thirteenth `merge_gate` check blocks auto-merge when the PR, or
any issue it closes, carries `requires-human-review`. `linked_issues.json` has no labels
(`closingIssuesReferences` carries number, title and url), so it needs one
`gh issue view <n> --json labels` per linked issue. Treat an empty `linked_issues` as allowed
and an unreadable one as blocked, the same "absence is not a bad value" rule the auto-merge
switches use. Create the label in triage's label block so a human can apply it before any run.

### Part 3: intake latency (choose one)

- **3a (S).** Enable the four `issue-triage-*` schedules (hourly, one issue per fire). The
  cost is up to one hour of latency, and each fire is a `max_concurrent_runs` slot for a few
  minutes.
- **3b (M), recommended long term.** The scheduler gains a second, **lease-less** dispatch
  kind: when the inventory loop sees a new `needs-triage` issue, or a `needs-info` issue with
  a human reply, it fires one `issue-triage` run with `issue_number` as an input.
  `issue-triage`'s `acquire` then takes the input instead of choosing. Latency is about 60 s.
  This is also the first lease-less dispatch path, which B6's scouts reuse.

## Record the decision

Add an amendment section to `docs/adr/0012-architecture-review.md`: D8 is amended so that
Architecture issues stop at `proposed`. Give the reason (box-hours spent on declined work,
and the operator's requirement) and keep the merge block.

## Verification

- `ops/test-task-gates.sh`: `apply_ready` with an `architecture` label adds `proposed` and not
  `agent`. Without it, behaviour is unchanged. Check 13 fixtures cover blocked, allowed, empty
  and unreadable.
- `fabro validate` on `issue-triage` and `arch-review` stays clean.

## Open questions

1. Should the `arch-review` summary list `proposed` issues as links, so the Discord message is
   the approval queue? Suggest yes. It costs one line.
2. Should `proposed` issues expire? Suggest: closed as "not planned" after 30 days with no
   promotion, recorded in A3's rejected-proposal ledger.
