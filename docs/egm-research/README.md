# EGM research for the software factory

**Status:** research complete, recommendations not yet approved. **Research date:** 2026-11-07.

## Reader and intended action

This series is for the engineer who changes Ralph, the Refuter, or the scheduler's measurements.
The reader should be able to select a first experiment without weakening the existing merge gates.

The main recommendation is to port EGM's review state and plan-assurance ideas. Do not port its
human approval gates, free-form result handling, or automatic contest behavior.

## Documents

1. [EGM mechanisms](01-egm-mechanisms.md) explains what the plugin specifies, how its parts fit,
   and which claims its tests support.
2. [Ralph comparison](02-ralph-comparison.md) maps those mechanisms to Ralph and identifies real
   gaps, existing equivalents, and harmful imports.
3. [Adoption plan](03-adoption-plan.md) defines staged experiments, contracts, measurements, and
   stop conditions.
4. [Planning a feature with EGM](04-planning-a-feature-with-egm.md) is the operator's guide to
   using the EGM plugin to design a feature before the factory planning skill turns it into
   issues, with the [factory readiness standard](factory-readiness-standard.md) as a custom
   EGM standard. Added 2026-11-07, after a comparison with live factory data showed that most
   held PRs trace back to how the issue was written, not to Ralph's review loop.

## Scope and evidence

The review used these primary sources:

- EGM commit `e9e1f06aa4e26e95cd4ed37210a0632202c429e9`, including its skills, personas,
  standards, design records, tests, and relevant Git history.
- Fabro commit `d59770a12af9f5df546566bc8d1f28084416b256`, including Ralph's graph and prompts,
  Stage manifest and schemas, gate tests, ADRs, invariants, and trial records.
- A local run of EGM's offline unit suite: **340 passed, 2 skipped**. No live EGM model review was
  run. The test record's limits are covered in the mechanism analysis.
- Ralph's recorded trial, not a new production measurement: 38 clean merges in 109 Ralph runs,
  against 27 in 124 Backlog runs. Ralph used 49% fewer box-hours per clean merge. The trial did
  not isolate which Ralph mechanism caused the improvement.

EGM means Elephant-Goldfish Model. "Prompt-specified" means a Markdown skill instructs an agent to
perform a step. It does not mean deterministic code enforces that step.

## Main findings

### 1. Reviewer history is the clearest Ralph gap

Ralph's Refuter already receives earlier findings, their Task states, and a delta between review
trees. A passed Refuter finding can return only with new evidence in that delta.

The standards and spec reviewers have no equivalent history. Their public ids restart each round.
Ralph's own design record says, "`refute_history.md` covers the Refuter; nothing covers the
reviewers." This can create repeated Tasks and can hide a fix that did not work.

Adopt EGM's distinction between settled and recurring findings for both review axes. A fixed
finding must re-open when it recurs. Repeated contradictory findings should produce a churn signal,
not more blind Iterations. Start by recording and measuring fingerprints. Do not begin with fuzzy
deduplication or automatic passes.

### 2. Ralph has no independent review of the implementation plan

Ralph's planner reads the issue and repository, writes the Task list, and then coding starts. The
planner is fresh from the coders, but no fresh reviewer challenges the planner's interpretation,
chosen approach, or Task handoff.

EGM separates architecture review, naive-reader comprehension, and implementation readiness. Apply
those lenses to a selected subset of Ralph plans in shadow mode. Use the Stage manifest to give
each lens bounded inputs. Do not add design-document bureaucracy to routine bug fixes.

### 3. The factory needs executable reviewer evaluations

Fabro has extensive executable gate tests. It does not have the proposed seeded-defect suite for
judging reviewer recall and false blocks. EGM has useful behavioral scenarios, but its offline
suite checks their structure rather than executing the review behavior.

Build the missing evaluation harness before changing review prompts, models, round counts, or
sampling. Include known-bad branches, valid controls, repeated findings, and a missing reviewer.
Measure both missed defects and false blocks.

### 4. Deterministic synthesis belongs after independent reviews

EGM's synthesizer deduplicates findings and reports each reviewer as `completed` or `not run`.
Ralph already fails closed when a reviewer produces no valid result. Preserve that stronger rule.

Ralph can still deduplicate exact duplicate Tasks after all reviewers finish. `review_gate` is the
right place. Never share one axis's findings with another reviewer before it submits. Never let a
model synthesizer replace the gate's verdict computation.

### 5. More review rounds are not the default answer

EGM specifies two consecutive clean rounds and a five-round cap. Ralph opens a PR after its first
clean Refuter and review round, with three Refute visits available for repairs and one extra review
pass.

Do not require two clean rounds on every Ralph run. That would add a full validation, Refuter, and
two review stages to successful runs. Test extra sampling only on a measured risk class, and only
after the evaluation harness establishes its benefit.

## Recommended sequence

| Order | Work | Initial routing effect |
| --- | --- | --- |
| 1 | Record reviewer fingerprints, recurrences, and churn | None, shadow measurement |
| 2 | Build the executable reviewer evaluation set | None, opt-in test harness |
| 3 | Add same-round exact duplicate suppression and coverage records | Limited Task creation, gate behavior preserved |
| 4 | Run selective plan reviews in shadow mode | None, findings recorded only |
| 5 | Enforce validated recurrence, churn, and plan-review rules | Bounded changes after measurement |
| 6 | Test extra sampling or adaptive clean-round rules | Only a measured risk class |

The detailed contracts and stop conditions are in the [adoption plan](03-adoption-plan.md).

## What Ralph must keep

- Mechanical Task validation and full Final validation.
- Diff-hygiene counters and tree matching.
- Input receipts, sealed paths, and schema-checked outputs.
- A cross-family, read-only Refuter whose verdict code computes.
- Evidence filtering for `not_met` criteria and defects.
- Exact-tree evidence for command criteria.
- Fail-closed behavior for a missing Refuter or reviewer result.
- The three-visit Refute budget and bounded extra review pass, until measurements support a change.
- No waiting human gate inside Ralph. A human decision ends at the existing triage or handoff path.

## What not to import

- A Refuter contest path. EGM's `CONTEST` category would weaken the independence that ADR 0013 D5
  deliberately protects.
- Automatic acceptance of contested or accepted-risk findings. An unattended run has no human who
  can accept that risk.
- A nested agent synthesizer. Ralph's sequential Stage I/O model and command gates already provide
  the required aggregation point.
- Free-form findings as the sole contract. Fabro's schemas and receipts are stronger.
- A universal zero-decision rule or strict planned-file allowlist. Ralph deliberately allows
  necessary caller and integration edits beyond the Task's file hints.
- Mandatory design documents for every issue. EGM itself excludes minor bug fixes from that rule.
- TRIZ pattern generation as a blocking review. Any inventive review should remain advisory and
  should name secondary costs.

## Source entry points

- [EGM review loop](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/skills/egm-review-loop/SKILL.md)
- [EGM resolution rules](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/skills/egm-resolve/SKILL.md)
- [EGM test record](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/TESTING.md)
- [Ralph overview and contracts](../ralph/00-overview-and-contracts.md)
- [Ralph invariants](../agents/invariants-ralph.md)
- [Ralph trial result](../ralph/review-2026-11-02.md)
- [Refuter decision, ADR 0013](../adr/0013-merge-gate-hardening.md)
- [Ralph decision, ADR 0029](../adr/0029-ralph-workflow-trial.md)
