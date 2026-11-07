# Adoption plan

**Status:** proposed research plan, not an approved implementation series. The first two work
items are measurement and test infrastructure. They do not change production routing.

## 1. Goals and non-goals

### Goals

- Reduce repeated reviewer Tasks and detect review churn.
- Add independent assurance before selected implementation plans reach coders.
- Measure reviewer recall, false blocks, and cost before changing review policy.
- Preserve independent discovery while avoiding duplicate implementation work.
- Keep every change bounded, observable, and reversible.

### Non-goals

- Replace Ralph with EGM.
- Add a waiting human gate to Ralph.
- Let the Refuter, planner, or coder contest away an independent finding.
- Require two clean rounds on every run.
- Add mandatory design documents for routine fixes.
- Add a nested agent synthesizer or parallel agent stages.
- Change deployment, provider, or merge infrastructure for the first experiments.

## 2. Invariants for every work item

1. Command nodes compute routes and verdicts. Agents submit findings, not merge decisions.
2. A missing reviewer result never counts as a clean review.
3. A current blocking finding stays in `not_fixed`, regardless of earlier ids or Tasks.
4. A recurring finding after a passed Task indicates a failed fix or regression. It never proves
   that the finding is settled.
5. Reviewers do not read Refuter findings, the other axis's findings, coder reasoning, or Task
   notes before submitting.
6. Deduplication happens only after all independent results arrive.
7. Every new reviewer input has a Stage manifest declaration and an explicit sealing policy.
8. Every new round or repair path has a mechanical cap.
9. Every human decision follows the existing triage, stuck, or PR handoff path. It does not hold a
   coder box while waiting.
10. Production changes keep the current evidence, validation, hygiene, and merge gates unless a
    measured proposal explicitly changes one.

## 3. Work item A: reviewer history and churn measurement

### 3.1 Purpose

Establish how often standards and spec findings recur, which recurrences follow a passed Task,
and how often the requested repair changes. This addresses the gap named in Ralph's own design
record without assuming that recurrence is always waste.

### 3.2 Prospective data contract

Add a command-owned `/tmp/fabro/review/review_history.json`. An append-only occurrence can use:

```json
{
  "axis": "standards",
  "round": 2,
  "fingerprint": "sha256:...",
  "finding_id": "STD-004",
  "message": "the normalized finding text",
  "suggestion": "the requested repair",
  "files": ["src/example.py"],
  "line": 42,
  "task_id": "rv2s1",
  "task_status_at_write": "open",
  "reviewed_tree": "git tree id",
  "evidence": "repository evidence from the finding"
}
```

Use three fingerprints during measurement:

- `axis_message_fingerprint`: axis plus normalized message, for recurrence;
- `axis_scope_fingerprint`: axis plus normalized message plus sorted file paths, for scoped recurrence;
- `shared_scope_fingerprint`: normalized message plus sorted file paths, for same-round duplicates.

Normalization should lowercase text and collapse whitespace. Normalize punctuation spacing without
removing operators or values. Keep identifiers, numbers, requirement text, and paths. Exclude the
public finding id and line number. Keep the suggestion outside the identity so incompatible repairs
remain visible.

History occurrences are append-only. A separate projection maps Task ids to their current states.

Do not use embeddings or model-generated semantic keys in the first implementation. They would
make deduplication behavior difficult to reproduce and test.

### 3.3 Collection changes

`review_gate` appends occurrences after it creates Tasks. It already knows the axis, finding,
round, Task id, and current tree.

`review_prep` refreshes the current-state projection and archives the previous result before
deleting it. The archive preserves the evidence that a later history view needs. `prep` clears all
history at the start of a run.

The scheduler's summary should publish counts, not raw findings:

| Metric | Meaning |
| --- | --- |
| `review_occurrences` | Total blocking findings by axis |
| `review_repeat_exact` | Occurrences matching an earlier fingerprint on that axis |
| `review_repeat_after_pass` | Repeats whose earlier Task passed |
| `review_same_round_duplicates` | Exact duplicates across sources in one round |
| `review_churn_candidates` | Repeats after a pass with a changed suggestion or alternating tree state |
| `review_history_rounds` | Rounds with complete history for both axes |

A repeat is not automatically waste. It can be a valid regression, a false positive, or an
imprecise fingerprint. Classify a sample by reading the associated diffs before enforcing rules.

Historical runs can support the baseline only when their review contracts, Tasks, and events remain
available. Otherwise, start the baseline prospectively. Do not infer fingerprints from truncated
PR summaries alone.

### 3.4 Shadow history inputs

First compare gate-only history with history-informed reviewers. Own-axis history can anchor a
later reviewer or suppress a different finding. Keep the gate-only design when recall or finding
diversity falls.

After collection proves stable, test these optional reviewer inputs:

- `standards_history`: prior standards occurrences, Task states, and the latest standards delta;
- `spec_history`: prior spec occurrences, Task states, and the latest spec delta.

Each input must exclude Refuter data and the other axis's findings. The existing
`refute_history.md` remains sealed.

The reviewer prompt gains EGM's recurrence rule:

- A finding whose earlier Task passed can return only with new evidence in the delta.
- Quote that delta evidence in the finding.
- A finding whose Task is open remains under review on the current tree.
- Do not reuse an old public id to claim that a new finding is fixed.

This is initially advisory. `review_gate` continues to create every blocking Task and to list every
current blocker in `not_fixed`. Measure unsupported recurrences before adding deterministic filters.

### 3.5 Enforcement candidate

After evaluation, a conservative rule can mark an exact repeat after a passed Task as
`recurred_after_pass`. It must remain a Task or a blocker. The rule may stop extra work only when
two independent findings demand incompatible outcomes and the gate can preserve both.

That last case is churn. Route it to the existing human handoff with both findings, both repairs,
and the two tree ids. Never let the newest reviewer silently win.

## 4. Work item B: executable reviewer evaluation harness

### 4.1 Purpose

Create the missing quality measurement for prompt and model changes. Shell fixtures prove that
routing follows a result. They do not prove that a model produces the right result.

### 4.2 Fixture set

Use a synthetic public repository with small, deterministic branches. Include at least:

| Fixture | Expected behavior |
| --- | --- |
| Valid implementation with focused tests | No blocking finding |
| Off-by-one boundary error | Refuter or spec identifies wrong behavior |
| Missing caller update | Readiness or spec identifies incomplete integration |
| Acceptance-criterion substitution | Refuter and spec preserve the original requirement |
| Deleted or weakened test | Hygiene blocks deterministically |
| Valid test moved or strengthened | Hygiene does not false-block |
| Command criterion validated on the same tree | Refuter can mark it met with the evidence chain |
| Command criterion validated on another tree | Refuter leaves it unverifiable |
| Vague implementation plan | Readiness identifies the remaining design choice |
| Plan with a proposed new file | Reviewer does not call the new file missing |
| Existing helper that satisfies the plan | Architecture reviewer identifies reuse |
| Finding repeated after a passed fix | Reviewer requires new delta evidence |
| Two incompatible repair requests | Gate reports churn rather than selecting a winner |
| Missing reviewer output | Gate records `not run` and blocks |
| Reused `STD-001` with different content | Gate treats it as a new finding |

Add EGM-inspired prompt cases for a comprehension paraphrase and a false compromise. Do not adopt
EGM's pattern assertions without checking that they measure useful behavior rather than wording.

### 4.3 Two test layers

**Offline layer.** Execute extracted shell nodes against fixture results. Cover history creation,
fingerprints, missing results, exact duplicates, reused ids, churn, and cap behavior. This belongs
in `ops/gate_tests/` and runs with the normal offline gates.

**Opt-in model layer.** Run real prompts against the fixture branches with recorded model and
provider versions. Save output contracts, token use, duration, and native tool evidence. Test both
valid results and failure injection. Do not turn a prompt answer into a pass without the same
receipt and gate checks used in production.

Run the model layer before a reviewer prompt, model, recurrence, deduplication, or round-policy
change. A weekly scheduled run can detect drift, but the first harness should be operator-invoked.

### 4.4 Evaluation metrics

Report by fixture, reviewer, model, and provider:

- defect recall;
- valid-control false-block rate;
- unsupported finding rate;
- recurrence after a passed fix;
- missing-evidence rate;
- exact duplicate rate;
- duration and token cost;
- invalid or missing result rate.

Do not optimize only for more findings. A reviewer that blocks everything has perfect recall and
no factory value.

## 5. Work item C: deterministic synthesis and coverage records

### 5.1 Aggregation point

Use `review_gate`, not a new agent. It receives the independent standards and spec contracts and
can read the Refuter-created Tasks already in `tasks.json`.

The sequence is:

1. Validate all required reviewer receipts.
2. Record each reviewer as `completed` or `not run`.
3. Stop or hold when coverage is incomplete, preserving current behavior.
4. Compute fingerprints over current blocking findings.
5. Group only exact duplicates for Task creation.
6. Preserve every original finding in reports and `not_fixed` calculations.
7. Write the merge contract with the existing rules.

### 5.2 First deduplication rule

Start with standards-to-spec duplicates. Two findings group only when normalized message and sorted
file scope match under the measured `shared_scope_fingerprint` rule. Keep both source ids and axes
in Task metadata.

Grouping beyond those two reviewers, including Refuter-to-review grouping, should wait for separate
evaluation. Standards and spec can use different language for the same underlying defect, but a
false group would leave real work undone.

The grouped Task should contain:

- every source finding id and axis;
- the original requirement or criterion;
- all cited files and evidence;
- every suggested repair that does not conflict;
- conflicting suggestions separately, which can indicate churn.

Do not remove original findings from PR reporting. Deduplication reduces implementation work, not
evidence.

### 5.3 Coverage record

Write a small `review_coverage.json` for observability:

```json
{
  "round": 2,
  "expected": ["refute", "standards", "spec"],
  "completed": ["refute", "standards", "spec"],
  "not_run": [],
  "tree": "git tree id"
}
```

The extra review pass should list only the axes expected to re-run. The retained result from the
other axis is not a new completion. This distinction prevents an old result from looking like new
coverage.

Production gating remains as it is today. The record explains coverage to the scorecard and does
not soften it.

## 6. Work item D: selective plan assurance

### 6.1 Review artifact and trigger

Review `task_list.json` directly. Do not create a second design document.

Select plans with deterministic signals available before coding, such as:

- several Tasks or many distinct file hints;
- authentication, authorization, data migration, concurrency, or public-contract surfaces;
- a new cross-module interface.

Define the exact trigger from baseline data. In shadow mode, review an A/B sample of eligible
plans and record what would have blocked. Check active trial arm assignments before choosing a new
split bit.

Routine single-file fixes should not pay for three hosted plan reviews.

### 6.2 Three lenses

Use separate fresh stages in both shadow and enforcement modes. They remain sequential because one
sandbox has one Stage I/O identity file.

| Stage | Inputs | Output focus |
| --- | --- | --- |
| `plan_architecture_review` | Issue, plan summary, Task bodies and coverage, repository map, orientation, relevant standards | Root problem, reuse, material alternatives, false compromises |
| `plan_comprehension_review` | Issue and complete Task list, without planner reasoning | Paraphrases, missing connections, undefined project terms, unclear handoffs |
| `plan_readiness_review` | Complete Task list, cited source, test configuration, criterion checks | Requirement coverage, current references, remaining decisions, test and command adequacy |

The architecture reviewer can read code to verify reuse. The comprehension reviewer should behave
like a fresh coder and explain the proposed work and current system. The readiness reviewer must
not mark proposed new files as missing current files.

A shared finding shape can include `severity`, `requirement`, `message`, `files`, `evidence`, and
`suggestion`. Comprehension output also includes `proposed_paraphrase` and
`existing_system_paraphrase`. Schemas should require evidence for errors and allow no free-form
verdict outside the enumerated decisions.

### 6.3 Shadow mode

The review gate writes results and metrics but takes no repair route. The original Task list
continues unchanged. Measure:

- findings per reviewed plan;
- unsupported findings from the opt-in evaluation set;
- findings later confirmed by the Refuter or reviewers;
- plan defects found before coding that no later stage repeats;
- duration and hosted cost;
- downstream Iterations, review Tasks, holds, and merges.

A shadow finding that later causes a coding failure is useful evidence. A shadow finding that only
requests a different preference is not.

### 6.4 Enforcement mode

Only after the shadow result supports it:

1. Route eligible `ready` plans through the three reviews and a command gate.
2. Give the planner one bounded repair turn with the consolidated findings and previous Task list.
3. Re-run the plan reviews once after repair.
4. Block coding when an error remains, a required reviewer is missing, or the plan still contains
   a contradiction.
5. Route contradictions to triage and other human-only decisions to `mark_stuck`.
6. Keep warnings advisory and preserve all findings in run artifacts.

Do not give the planner an automatic `CONTEST` pass. A contest that needs external authority is a
human decision. A contest based on repository evidence can be tested later as a separate proposal
with its own precision measurements.

Set stage timeouts from shadow measurements. Do not inherit EGM's interactive stage limits without
measuring the selected Fabro models and prompts.

### 6.5 Inventive review

Keep false-compromise analysis inside the architecture lens and make it advisory. Require a real
trade-off, a concrete alternative, and secondary costs. Do not add a pattern catalog as a source of
mandatory Tasks.

If the architecture lens mostly produces speculative complexity, remove this part rather than
adding a zoom-out loop to prune its own additions.

## 7. Work item E: adaptive review depth

Defer this work until the evaluation harness and recurrence data exist.

Possible experiments include:

- a second independent Refuter sample for a defined high-risk class;
- a second clean review round for large or contract-sensitive changes;
- A/B sampling of one review axis when its false-negative rate is measurable;
- early churn termination when a conservative detector fires.

Each experiment needs a defined risk class, a control group, a cost ceiling, and a false-block
measurement. Do not use model-rated `risk` alone to select more review. That rating already blocks
automatic merge above three, and it can vary by model.

Keep the first-clean-round behavior for ordinary runs. EGM's two-clean-round rule addresses fresh
reviewer sampling, but Ralph's extra full round has a known multi-stage cost.

## 8. Work item F: living context and drift

Do not build a separate EGM drift pipeline yet. First measure the existing context systems:

- orientation age and regeneration failures;
- orientation claims corrected by the planner;
- context-pack claims corrected by the planner or coder;
- accepted versus rejected Learnings;
- repeated Learnings that indicate an unowned documentation update.

If stale orientation causes repeated plan defects, add a targeted drift check to the existing
background orientation package. It should compare documented contracts with current code and
refresh the pinned orientation issue.

If automated documentation updates later become allowed, use generated sentinel blocks. Human
text outside those blocks must remain unchanged. Ralph itself should continue proposing Learnings
rather than editing repository guidance.

## 9. Recommended implementation sequence

| Slice | Content | Production behavior | Exit evidence |
| --- | --- | --- | --- |
| 0 | Prospective recurrence and duplicate metrics | None | Baseline report and sampled classifications |
| 1 | Offline and opt-in reviewer evaluation harness | None | Recall, false-block, and cost baseline |
| 2 | Reviewer history inputs and coverage records | Prompt guidance only | Unsupported-repeat rate and isolation fixtures |
| 3 | Exact same-round Task deduplication | Fewer duplicate Tasks | No lost blocker in fixtures, lower duplicate work |
| 4 | Conservative churn routing | Early handoff only on proven pattern | Churn fixtures and sampled production verdicts |
| 5 | Selective plan review in shadow mode | None | Usefulness, precision, and cost report |
| 6 | Bounded plan repair and enforcement | Selected plans only | Better downstream outcomes within cost guardrails |
| 7 | Adaptive review-depth trial | Defined risk class only | Measured recall gain worth its cost and false blocks |

Slices 0 and 1 can proceed independently. Slices 2 through 4 should reuse the same history
contract. Slice 7 is optional and should not block the earlier work.

## 10. Measurement and stop conditions

Use repository and coder-pool strata where sample sizes allow. Track:

- clean merges per run;
- box-hours and hosted cost per clean merge;
- operator commits and hand-merged held PRs;
- review Tasks per run and per source;
- Refute visits and extra passes per run;
- exact repeats and repeats after a passed Task;
- unsupported findings and false blocks;
- plan-review findings confirmed by later stages;
- reverts or same-line fixes after merge, when the scorecard supports them.

Stop or revert a change when:

- a current blocker disappears without a passed Task or explicit human handoff;
- reviewer isolation weakens;
- valid controls gain a material false-block rate;
- merge rate falls beyond the agreed experimental guardrail;
- box-hours or hosted spend per clean merge exceeds the agreed guardrail;
- recurrence falls only because the gate hides findings;
- plan review produces mostly preferences or speculative architecture.

Use the existing +20% box-hours guardrail as the starting discussion point, not as proof that a
smaller regression is acceptable. Hosted spend also needs an explicit ceiling before plan reviews
or extra sampling ship.

## 11. Expected file areas for implementation

No files in this section change during the research task.

| Area | Likely changes |
| --- | --- |
| Ralph graph | History collection, coverage records, optional plan-review stages and bounded routes |
| Ralph prompts | Recurrence guidance and the three plan-review lenses |
| Stage manifest and schemas | New reviewer inputs, history sealing, and plan-review contracts |
| Gate tests | Fingerprint, recurrence, deduplication, churn, coverage, and plan-review fixtures |
| Scheduler summary and scorecard | Aggregate recurrence, coverage, plan-review, and cost metrics |
| Evaluation fixtures | Synthetic branches and opt-in live reviewer runner |
| Ralph invariants and ADRs | New contracts and the measured decision for each enforced behavior |

New stages can use the existing manifest shape, so these proposals do not inherently require a
`fabro-io` manifest-struct change. Any change to `ops/fabro-io/src/manifest.rs` would trigger the
repository's binary-before-manifest deployment rule.

Graph and prompt changes require the normal offline gates and host validation before a push.
Scheduler metric changes require their own tests and deployment steps.

## 12. Decisions the research supports now

1. **Approve measurement first.** Reviewer history is a documented Ralph gap, but its frequency
   and precision are unmeasured.
2. **Build the evaluation harness before policy changes.** EGM supplies scenarios, while Fabro
   supplies the deterministic gate infrastructure.
3. **Prototype plan review on selected plans.** This is the largest missing stage in Ralph's
   lifecycle, but its value and cost are unknown.
4. **Keep the Refuter uncontested and fail closed.** EGM's collaborative resolution model does not
   fit an unattended merge gate.
5. **Defer universal extra rounds and semantic deduplication.** Both can increase cost or hide
   work without reliable evidence of benefit.

## Related sources

- [EGM convergence rules](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/skills/egm-review-loop/SKILL.md)
- [EGM resolver](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/skills/egm-resolve/SKILL.md)
- [EGM readiness standard](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/resources/document_standards/implementation-readiness-standard.md)
- [Ralph reviewer gap](../ralph/00-overview-and-contracts.md#risks-to-watch)
- [Ralph invariants](../agents/invariants-ralph.md)
- [Ralph gate tests](../../ops/gate_tests/test_ralph.py)
- [Factory evaluation proposal](../factory-roadmap/B1-factory-scorecard.md)
- [Refuter evaluation proposal](../factory-roadmap/A5-cross-family-refuter.md#measure-before-trusting)
