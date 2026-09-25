# A5 · A cross-family adversarial refuter in front of the squash-merge

**Status:** proposed. **Axis:** quality. **Effort:** M. **Feasibility:** high.
**Depends on:** a new provider key (Anthropic recommended). **Better with:** A4 (the refuter
reads `hygiene.json`), B3 (the checklist goes through the submit tool), B1 (catch-rate
measurement).

## Problem

The gate in front of an unattended merge into `main` is one vendor and one lineage:

| Node | Class | Model (both importing stylesheets) |
|---|---|---|
| `standards` | `.merge-standards` | `glm-5.3-flash` |
| `spec` | `.merge-spec` | `glm-5.3` |
| `review_fix` | `.merge-fix` | `glm-5.3` |
| `ci_fix_t1` | `.ci-fix` | `glm-5.3-flash` |

(`backlog/workflow.fabro:156-159`, `pr-review/workflow.fabro:64-67`.) The rework ladder's top
tier is the same `glm-5.3`. `fix_gate` checks that each error finding is **cited** in
`fixes_applied` or `not_fixed`, never that the fix is right. So a reviewer can approve work
from its own model family, and `review_fix` can "fix" a finding by writing that it did.

no_human's evidence for the alternative: a **different model, in a session that never saw the
coder's transcript, told to refute "done"**, sent back 505 of 1,709 coder "done" claims
(30%) over 65 days. wsff.md makes the same point from the other side: lights-off
self-review grades itself generously.

## Design

### The node

`refute`, an agent node in `_shared/review-merge/`, placed **after `validate` is green and
after A4's `hygiene`, before `deliver`**. Never inside the `validate → review_fix` CI loop,
where it would pay for reviews of diffs CI is about to reject (rev 2 correction 9).

**Inputs (and only these):** the issue title and body with the triage decisions,
`linked_issues.json`, the acceptance criteria, `git diff origin/main...HEAD` (capped, largest
files summarised by name and line counts), and `hygiene.json`. **No** coder transcript, **no**
reviewer verdicts, **no** `fix_result.json`. Independence is the point.

**Instruction:** assume the work is not done, and try to prove it. For each acceptance
criterion, output `met | not_met | unverifiable` with the `file:line` that shows it. For each
blocking defect, output severity, `file:line` and a one-sentence failure scenario. No numeric
score. A `pass` requires every criterion `met` and no blocking defect.

**Output:** `/tmp/fabro/review/refute.json`, written through B3's `fabro-submit refute ...` once
B3 exists, and until then by the agent, checked by a `refute_gate` with the usual one-repair
counter.

**Routing (v1):** `pass` → `deliver` as today. `fail` → `deliver` still runs, so the checklist
is posted on the PR and the PR exists, then `merge_gate` blocks with a new check that reads
`refute.json`. **No loop back to `review_fix` in v1.** The model whose work was refuted does
not get to argue with the refutation. Revisit once B1 shows the refuter's precision.

### The model

Anthropic, because it is the family least like GLM and Kimi. A Sonnet-class model for
ordinary PRs, and Opus for PRs labelled `architecture`, using a second class `.refute-arch`
chosen by label in the stylesheet, or two nodes. The cost is one PR-sized read per merge
attempt, about 30–80k input tokens.

### Deploy pitfalls (from `AGENTS.md`)

- `.refute` needs an explicit rule in **both** `backlog` and `pr-review` stylesheets. A missing
  rule inherits `*`, which in `backlog` is a local coder box, so the gate is silently
  downgraded rather than failing.
- Storing `ANTHROPIC_API_KEY` configures fabro's **built-in** `anthropic` provider. That is the
  intended provider here, but run `fabro model test -m <id>` to prove which provider won the
  id, and check that `Rejected reloaded` prints `0` after any overlay edit.
- Add a `[run.model.fallbacks]` row for the refuter model that fails **closed**. A refuter
  that falls back to `glm-5.3` is the monoculture again. Prefer no fallback, so a provider
  outage blocks auto-merge (a human merges) rather than weakening the gate.

## Measure before trusting

Adopt no_human's reviewer-recall method, run under B1's harness:

1. Build 20 seeded-defect PRs on a scratch repo: removed validation, an off-by-one, a test that
   asserts nothing, a swallowed error, a wrong acceptance criterion.
2. Run the current merge phase and the refuter on each, and record catch rate and false-block
   rate.
3. Keep the suite. Re-run it on every model or prompt change to the merge phase.

## Open questions

1. Should the refuter run on **task-level** reviews too? No for v1. Its value is at the gate in
   front of `main`.
2. Should a `fail` post the checklist as a PR review with "request changes"? Suggest a plain
   comment, so GitHub branch protection is not coupled to it.
