# 05: ADR amendment, plan-trial note, docs

## Steps

1. `docs/adr/0013-merge-gate-hardening.md`: append a new section at the **end** (in the
   style of the existing amendments: a **Status:** line, then **Why.** and **Decision.**
   paragraphs, then Consequences). Title:
   `## Amendment (2026-10-15): The Refuter credits CI for command criteria (#40)`.
   It must state:
   - It amends D4: the Refuter's inputs gain `validation`.
   - The rule from task 02, in two or three sentences.
   - Missing evidence stays `unverifiable` and still blocks.
   - `.fabro/test.toml` checks are not evidence.
   - Options 3, 4 and 5 were not adopted, each with its one-line reason from D3 to D5.
   - The over-blocking trigger (D9), which **adds to** the 2026-10-14 luna amendment's
     consequences: if `unverifiable`-only first verdicts stay above 5 a day across
     repositories for 3 consecutive days after this change, the choice of Refuter model is
     reopened.
   - The two new `refute_gate` fields, and that the scorecard reads them.
2. `docs/plan-trial/README.md`: add one dated line in the most fitting place (near the
   metrics table, or in a notes/changes section). It must say: "On 2026-10-15 (#40) the
   Refuter began crediting `ci.sh` for command criteria. Its first-verdict metric changes
   at that deploy for both arms equally, so compare arms within the same window, not across
   it."
3. `docs/agents/invariants-merge.md`: if it lists the Refuter's inputs or `refute_gate`'s
   outputs, add `validation`, `validated_tree` and the two new fields there. If it lists
   neither, leave it alone.
4. `docs/refuter-evidence/00-overview-and-contracts.md`: set each task 01 to 05 to
   "done (commit `<short sha>`)" in the task table.

## Done when

`make check-fast` passes. Commit:
`docs(adr): 0013 amendment, the Refuter credits CI for command criteria (refuter-evidence 05, #40)`.
