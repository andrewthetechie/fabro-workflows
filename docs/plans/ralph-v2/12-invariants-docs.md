# Task 12: invariants, docs and the ADR

Read `00-overview-and-contracts.md` first, and load `/writing-for-agents`. Every fact must match the
**built** code of tasks 05-11: read the code, not only this plan. Where they differ, report it; do not write
either version.

## Steps

1. **`docs/adr/0034-ralph-v2-parallel-trial.md`**: replace the whole file (untracked today) by
   `assets/adr-0034.md`.
2. **`docs/agents/invariants-ralph-v2.md`**, in `invariants-ralph.md`'s form (the rule; what happens
   otherwise). It opens by saying that v1's file applies to every node `R10_V2_COPIES` lists. One row each:
   who writes `tasks.json` and what `reviewing` means; `pick` skips the cap for a reviewed Task; `pick`
   publishes `task_step` and `record` `review_due` on every path; `task_plan_gate` never publishes `rung`;
   patches on every pre-PR Iteration, and why `task_base_sha` is after the merge; the render is one program
   in two nodes; the receipt rule and the check's table; `max_retries=0` and R19; what `stage-end` covers and
   what the check counts; `state/K.served` is the gate's only proof; both reviewers every round; the Sealed
   lists, and the Refuter's two additions; an Iteration is sealed from `task_review/` (it could grade
   itself); a review never touches `misses`; the evidence rule, disputes and repeats; recovery through `pick`;
   the Task review is not a copy of the Refuter; the order of the hosted round and `review_prep`'s exhausted
   test first; the `not passed when the loop ran out` prefix; `resolve_merge` on the box; the dispatch payload
   and the manifest size.
3. **`docs/agents/invariants-graph.md`**: the R19 row; "the three coder loops" becomes four where
   `ralph-v2` carries the claim (R15), with the list. **`invariants-host.md`**: unchanged beyond task 02.
   **`docs/agents/validating.md`**: the payload check and R19.
4. **`AGENTS.md`**: *Layout* ("Sixteen packages", and `ralph-v2` with one clause); the invariants list; the
   baseline `RalphV2 (68 nodes, 176 edges)` with its one warning, **"unverified until `make check-host`"**;
   `ralph`'s text unchanged; a paragraph after "The scheduler routes every issue ..." on
   `SCHEDULER_RALPH_V2_SHARE` and its kill switch; `WORKFLOW=ralph-v2` for the fire script.
5. **`ops/README.md`**: a *ralph-v2 trial* runbook: the share, the kill switch, what each trial line means, the
   stop rules, C11, the weekly command (`./ops/fabro-trial-review.sh --arms ralph,ralph-v2 --from ...`).
6. **`CONTEXT.md`**: terms **Task plan**, **Task review**, **Rework**, **Dispute**, in the file's form with an
   _Avoid_ line each (Task plan: not the **Task list**, not `plan`; Task review: not a **Refute round**).
   Extend **Iteration** and **Task list** to say "a `ralph` or `ralph-v2` run".
7. **`docs/USER-GUIDE.md`**: the section *The `ralph` trial, from GitHub* becomes *`ralph` and `ralph-v2`,
   from GitHub*: what a person sees that differs on an even issue (a PR can be held with
   `box review: ...` under *Unmet criteria*), and nothing about the method.
8. **`docs/README.md`**: the `docs/ralph-v2/` row becomes `docs/plans/ralph-v2/`, **"Built, not deployed"**.
   **`docs/ralph-v2-glm-design.md`**: its pointer becomes `docs/plans/ralph-v2/00-overview-and-contracts.md`.
9. **`skills/plan-factory-issues`** and **`skills/setup-repo-for-factory`**: only where the text says what one
   run does or which workflow the scheduler starts; `measure_sizes.py`'s query keeps `workflow = 'ralph'`.

## Done when

- `make check` passes. `grep -rn "docs/ralph-v2/" --include=*.md . ` matches only history files
  (`.scratch/`, `docs/ralph-v2/` itself, and this plan's own statement that it replaces it).
- Commit: `docs(ralph-v2): invariants, runbook, ADR 0034 and terms (ralph-v2 12)`.
