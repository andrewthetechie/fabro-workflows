# 02: architecture PRs auto-merge; `do-not-merge` holds any PR (#30)

## Changes

1. `_shared/review-merge/review-merge.fabro`
   - Comment 2b (`:706`): replace with `2b. no do-not-merge label (ADR 0012 D8 amendment)`.
   - The check at `:731-732`: change the jq to look for `do-not-merge`; message:
     `'The PR carries the do-not-merge label, so a human merges it.'`. Keep the fail-closed
     `|| echo bad` / `!= false` shape exactly.
   - Grep the render.jq/report text in this file for "architecture" and "ADR 0012" wording
     that says a human must merge; update it.
2. `merge-queue/workflow.fabro:232`: replace the architecture branch of the guard with the
   same `do-not-merge` check (it hands the PR to a human). Read
   `docs/agents/invariants-merge-queue.md` first: the guard and approval chain have
   invariants.
3. `backlog/workflow.fabro:1809-1810`: the `MERGE=` sentence for architecture issues must no
   longer say a human merges it. Either drop the branch (use the default sentence) or say
   "It merges automatically when every gate passes; add `do-not-merge` to hold it."
   Keep `:1526-1528` and `:1582-1584` (the PR still gets the `architecture` label; it is
   still useful for the scorecard).
4. `_shared/triage/triage.fabro:146` reads the `architecture` label for another purpose;
   leave it.

## Tests (`ops/test-task-gates.sh`)

Find the existing merge_gate architecture case(s) (`grep -n architecture
ops/test-task-gates.sh`). Replace with:
- an `architecture`-labelled PR with every other check passing -> `merge_eligible=true`;
- any PR with `do-not-merge` -> `merge_eligible=false` with the new reason;
- the `gh` failure (`bad`) case still fails closed.
Add the matching merge-queue guard cases if the suite covers that guard.

## Done when

`make check` passes. Commit: `feat(review-merge): auto-merge architecture PRs that pass every gate (#30)`.
