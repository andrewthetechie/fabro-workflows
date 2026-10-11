# 01: ADR amendments

Append a dated `## Amendment (2026-10-11): ...` section to each ADR. Do not rewrite the
original decisions. Status: accepted (G12).

1. **ADR 0012 D8** (`docs/adr/0012-architecture-review.md:159`). Title: "An Architecture PR
   that passes every gate merges automatically (#30)". Record: the data from #30 (96/176 PRs,
   all merged by the operator, p50 5.2h vs 2.4h, operator-commit rate 24% vs 31%); option B
   chosen, C and A rejected and why (the human judgment is at issue promotion; no size limit
   was asked for); the `do-not-merge` hold label (G2) as the human's per-PR brake; what is
   unchanged (`arch-review` still files issues; promotion to `agent` is still the human's).
2. **ADR 0013 D4** (`docs/adr/0013-merge-gate-hardening.md:70`). Title: "Post-deploy checks
   are outside the Refuter's input (#28)". Record: 8/18 refuter blocks were `unverifiable` on
   deployed-environment criteria and all were merged by hand; G3, G4, G5.
3. **ADR 0013 D5** (`:93`). Title: "One fix turn after a `not_met` (#29)". Record: 10
   `not_met` PRs, operator fixed several by hand; 50/183 PRs had operator commits. Why this
   is not "the refuted agent arguing" (it changes code; the refuter judges again from
   scratch, and the fixer cannot see or edit the verdict). G6-G11. Still: `invalid`,
   `unverifiable`-only and defect-only verdicts block with no turn; a second `not_met`
   goes to a human.

Also update `docs/README.md`: the `docs/architecture-review/` row says "An `architecture` PR
is never auto-merged" — amend that sentence to point at the amendment. Add a row for
`docs/gate-relief/` (status: implemented locally, not pushed).

Grep for other statements that architecture PRs are never auto-merged and fix them:
`grep -rn 'never auto-merged\|never merged automatically\|human merges' docs .fabro`.
Commit: `docs(adr): amend 0012 D8 and 0013 D4/D5 for #28 #29 #30`.
