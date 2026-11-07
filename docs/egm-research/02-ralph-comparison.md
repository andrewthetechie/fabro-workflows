# Ralph comparison

**Fabro source version:** `d59770a12af9f5df546566bc8d1f28084416b256`. This comparison uses the
current graph and prompts, not only the original Ralph design.

## 1. Ralph's current control flow

Ralph starts with one planner and a command gate. Each Iteration then receives one Task in a fresh
coder session. A command gate combines mechanical validation with the Iteration's receipt and
reported status. No per-Task LLM review decides whether the Task passed.

The pre-PR path is:

```text
plan -> plan_gate -> pick
                     |
                     +-> excerpts -> Iteration -> task validation -> record -> pick
                     |
                     +-> optional whole-branch pre-review, once per run
                     +-> final merge -> full Final validation -> criterion checks -> hygiene
                     +-> Refuter -> refute_gate
                     +-> standards -> spec -> review_gate
                                      |
                                      +-> new Tasks -> pick
                                      +-> pass -> open PR
```

The pre-review runs only on its A/B arm and can edit the branch. A command node keeps those edits
only when its checks pass. The other reviewers are read-only.

In the normal repair loop, the Refuter and both reviewers run in every Refute round. A Refuter
`hold` prevents review findings from becoming Tasks. Otherwise, `error` findings join the same
coder loop. Three Refute visits are available. When the Refuter passes, a blocking review finding
after the third visit receives one extra bounded review pass. An exhausted loop and that extra
pass follow separate routes.

After the PR opens, red CI can become at most two post-PR Iterations. The copied merge stage still
checks hygiene, review outcome, risk, unfixed findings, and the last Refuter verdict.

## 2. What the Ralph result does and does not show

The recorded trial found:

- Ralph: 38 clean merges in 109 runs, or 0.35 per run.
- Backlog: 27 clean merges in 124 runs, or 0.22 per run.
- Ralph used 6.96 box-hours per clean merge, against Backlog's 13.77.
- The result varied by repository and coder pool.

This supports Ralph as the standard lane. It does not show that its current review depth is
optimal. The trial compared whole workflows and could not attribute the result to one stage.

The trial also identified costs relevant to EGM:

- `unverifiable` criteria were the largest hold class.
- Review findings left unfixed formed another hold class.
- Three PRs were held by a review error found after the third Refute round.
- The design record explicitly warned that reviewer findings had no history mechanism.

The November amendments added criterion checks, reviews in every Refute round, and the extra
review pass. EGM research should address the remaining repeat and planning gaps, not reopen the
whole workflow.

## 3. Mechanism-by-mechanism comparison

| Concern | EGM | Ralph today | Assessment |
| --- | --- | --- | --- |
| Fresh implementation context | Fresh Elephant drafting session is separate from reviewers | Every Iteration is fresh and has no `thread_id` | Ralph already applies this to coding |
| Fresh review context | Mandatory Goldfish subagents | Separate Refuter, standards, and spec stages | Same principle |
| Review model independence | Persona isolation is primary | Refuter also uses a different model family | Ralph is stronger |
| Review input isolation | Explicit reading list and fresh context | Stage manifest, sealed paths, and read-only prompts | Ralph has more mechanical control |
| Host instruction isolation | Not guaranteed on every harness | Agent profile is shared, sealed files guard other agents' outputs | Different boundary; do not claim zero project context |
| Planning artifact | Concept brief and design document | Task list with acceptance, coverage, and ordered context | Ralph is execution-oriented |
| Independent plan review | Strategic, comprehension, critic, and readiness stages | None before the first Iteration | Direct gap |
| Task handoff | Detailed implementation section | Per-Task context with inspected line ranges and current excerpts | Ralph has a concrete coder handoff |
| Implementation freedom | Zero-decision rule and planned file boundary | File hints do not forbid necessary integration edits | Keep Ralph's flexibility |
| Per-Task validation | Outside EGM | Mechanical selected tests, with full-suite fallback | Ralph is stronger |
| Full validation | Outside EGM | `setup.sh && ci.sh` on the final tree | Ralph is stronger |
| Review evidence | Persona findings and rationale | Paths, scenarios, receipts, exact-tree command evidence | Ralph is stronger |
| Output validation | Natural-language reports, one bootstrap JSON schema | JSON Schemas, submit receipts, command gates | Ralph is stronger |
| Refuter history | Settled and fixed finding states | Earlier findings, Task status, dropped findings, and delta | Already present for Refuter |
| Standards and spec history | Same general loop state | No earlier-axis history | Direct gap |
| Recurring fixed finding | Re-open it | Refuter requires new delta evidence; reviewers lack the rule | Port to reviewers |
| Reviewer coverage | `completed` or `not run`; missing status prevents clean round | Missing or invalid result fails closed | Keep Ralph's stronger behavior |
| Same-round synthesis | Synthesizer deduplicates | `review_gate` appends Tasks, with no semantic deduplication | Selective opportunity |
| Convergence | Two clean rounds, five-round cap | First clean round can open; three Refute visits plus one extra pass | Do not copy without measurements |
| Churn | Detect a fix whose reversal resolves a new finding | No reviewer churn classification | Direct gap |
| Contest | Elephant can contest with checkable evidence | Refuted work cannot argue with the Refuter | Do not import to the Refuter |
| Accepted risk | Settled across rounds | Requires a human outside the unattended loop | Keep human authority explicit |
| Architecture alternatives | Required for feature designs | Planner chooses or returns `needs_human` | Selective plan-review opportunity |
| Scope enforcement | Planned write boundary is a convention | Task scope prompts, spec scope review, and safe pre-review scope checks | Do not add a simplistic allowlist |
| Learning after a run | Living documents and drift reports | Evidence-backed Learnings wait on a rolling issue | Existing equivalent |
| Current-context refresh | Bootstrap and drift report | Daily repository orientation and issue context packs | Existing equivalent |
| Behavioral evaluation | Scenario specifications and selected native probes | Executable gate tests, no seeded reviewer defect suite | Build the missing live evaluation layer |

## 4. Gap one: reviewer history and recurrence

### Current Refuter behavior

`refute_prep` builds history from Tasks whose source is `refute`. It includes each finding's title,
Task id, and Task state. It also includes findings dropped by the previous gate. From round two,
it computes a delta from the tree the previous Refuter judged.

The prompt applies these rules:

- A passed finding is settled unless new delta evidence justifies raising it again.
- An open finding is judged again on the current tree.
- A dropped finding needs the missing path or command evidence.

The gate still evaluates every submitted finding. It does not trust the agent's verdict. This is a
good implementation of EGM's fixed-versus-settled distinction.

### Current standards and spec behavior

The two reviewers receive the current diff, issue, and repository context. They do not receive
their earlier results or the states of the Tasks those results created. Their ids restart from
`STD-001` and `SPEC-001` on every visit.

`review_gate` creates round-prefixed Task ids, which prevents collisions in `tasks.json`. It also
lists every current blocking finding in `not_fixed`, even when an earlier finding with the same
public id passed. That behavior is necessary. Removing it would reintroduce the reused-id merge
bug described in the invariants.

### EGM lesson

Add reviewer history without changing that fail-closed accounting:

1. Compute a fingerprint from the axis and normalized finding content, with file paths when useful.
2. Record each occurrence, its round, its Task, the Task state, and the reviewed tree.
3. Give each reviewer only its own axis history and the delta since its last visit.
4. Require new delta evidence when a finding whose Task passed returns.
5. Treat recurrence as evidence that the fix failed, never as proof that it is settled.
6. Record when repeated findings request incompatible repairs. That is churn.

The first implementation should record fingerprints and metrics only. Exact-text recurrence can
support a later deterministic rule. Semantic recurrence needs evaluation before it affects routing.

History can remain gate-only. Feeding it back to a reviewer can anchor discovery or suppress a new
finding. Compare gate-only recurrence handling with history-informed reviewers before choosing the
latter.

### Isolation constraint

`refute_history.md` is sealed from both reviewers. Do not unseal it. Create separate
`standards_history` and `spec_history` inputs that contain no Refuter findings or other-axis
opinions. Command nodes should derive those files from prior review contracts and Task states.

## 5. Gap two: no independent plan assurance

Ralph's planner performs useful grounding. It separates verified facts, requested behavior,
proposed design, and open questions. It writes requirement coverage, acceptance statements, file
hints, ordered edits, and criterion checks.

No fresh agent then asks:

- Did the planner misunderstand the issue or current system?
- Is the chosen approach solving the root problem with existing components?
- Did the planner consider a material alternative?
- Can a fresh coder execute each Task without an unstated design decision?
- Do cited current paths and symbols exist?
- Does every requirement have a Task, an explicit exclusion, or a human decision?

EGM separates those questions across strategic, comprehension, and readiness reviews. Ralph can
apply the same separation to its Task list before coding.

This does not require a checked-in design document. The Task list is already the review artifact.
It also does not require every run to pay for three hosted reviews. Start with architectural or
high-risk plans and run the review in shadow mode.

A plan reviewer should distinguish current references from proposed files. EGM's codebase reviewer
has an explicit rule for this distinction. Without it, the reviewer will report every planned new
file as missing.

## 6. Gap three: duplicate work after independent findings

Independent Refuter, standards, and spec reviews can describe the same defect. Their independence
is valuable during discovery. Their Tasks do not all need independent implementation.

EGM places deduplication in a synthesizer. Ralph already has a deterministic aggregation point in
`review_gate`. It can group exact duplicates after all results arrive.

The safe order is:

```text
independent reviews -> complete result contracts -> gate-only comparison -> grouped Tasks
```

The unsafe order is:

```text
one review -> show its findings to the next review -> apparent agreement -> grouped Tasks
```

Only exact or conservatively normalized duplicates should merge in the first implementation. Keep
all original findings and source axes in the report. Keep the strongest evidence and requirement
text in the grouped Task. Never drop a current blocking finding from `not_fixed` merely because it
resembles a passed Task.

Fuzzy deduplication needs the evaluation harness first. A false merge can leave a requirement with
no Task and no human-visible block.

## 7. Gap four: churn has no explicit state

Ralph bounds cost with Iteration, rung, Refute, and extra-pass budgets. Those limits stop runaway
work, but they do not explain why a loop is repeating itself.

EGM defines churn as a finding at the scope of an earlier fix where reverting the fix would
resolve the new finding. Ralph needs a related signal for unattended operation:

- the same normalized finding returns after its Task passed;
- two reviewers require incompatible outcomes;
- a repair recreates the exact defect reported in an earlier round;
- the same files alternate between two states across rounds.

A churn signal should preserve both findings and their evidence. It should route to the existing
human handoff only after a validated rule fires. It must not silently select the latest reviewer
as the winner.

Start by publishing counts and reasons. Early termination is appropriate only when the rule is
conservative and covered by fixtures.

## 8. Convergence is different, not deficient

EGM requires two clean rounds because fresh reviewers may find different gaps. Ralph instead uses
a complete requirement checklist, two additional review axes, deterministic validation, and merge
checks. A clean first round already contains several independent judgments.

A second clean Ralph round would repeat:

1. final validation and criterion checks;
2. the Refuter;
3. standards review;
4. spec review.

That cost applies to every successful run if copied literally. The evaluation should instead ask
which changes have residual risk after one clean round. Candidate classes include large diffs,
public contract changes, data migrations, security-sensitive code, or a high model-rated risk.
Those classes need objective definitions before an experiment.

The current three-visit cap and one extra pass should remain until measurements support another
policy.

## 9. Planning freedom is different, not deficient

EGM's zero-decision rule aims to make a fresh implementer follow a human-approved design exactly.
Ralph gives coders a Task, acceptance statements, and an ordered plan, but allows departure when
evidence contradicts the plan or integration requires more edits.

That freedom is necessary. A planned file list can omit a caller, generated type, fixture, or test
that the change needs. A hard allowlist would turn correct integration into a scope failure.

Adopt the useful part instead. A plan-readiness reviewer should identify decisions that remain
unclear and current references that are wrong. The coder should still make local mechanical
choices and necessary integration edits.

## 10. Resolution is intentionally different

EGM lets the Elephant contest a finding with checkable evidence. This fits collaborative document
editing. It is dangerous in an unattended merge gate because the author can dismiss the review
that provides independence.

ADR 0013 D5 deliberately prevents the refuted agent from arguing with the Refuter. Keep that rule.
The existing `needs_human_review` outcome is the appropriate equivalent of EGM's `ESCALATE` when
repository evidence cannot settle a question.

Warning and info findings already remain non-blocking in Ralph. That covers the useful part of
`DECLINE` without letting the author rewrite reviewer severity.

## 11. Testing models are different

Fabro's gate tests execute the actual shell extracted from the graph. They cover receipts,
evidence filtering, round budgets, extra-pass state, reused ids, and merge behavior. EGM's offline
tests mostly establish package integrity and prompt text. Its behavioral scenarios offer good test
ideas, not a substitute for Fabro's gate fixtures.

Fabro still lacks the proposed live reviewer evaluation set. That suite should execute prompt and
model behavior against controlled repositories. It should report:

- defect recall by reviewer and model;
- false blocks on valid controls;
- repeated finding rates;
- missing-reviewer handling;
- evidence quality;
- duration and token cost.

Every later recurrence, deduplication, plan-review, or sampling change should run both the shell
fixtures and this behavioral suite.

## 12. Factory equivalents outside Ralph

Some EGM ideas already exist elsewhere in the factory:

| EGM idea | Factory equivalent |
| --- | --- |
| Concept feasibility review | Issue precheck and calibration |
| Codebase grounding before design | Context packs, repository maps, and code index |
| Bootstrap current documentation | Daily repository orientation |
| Periodic document-code drift check | Regenerated orientation, plus evidence-backed Learnings |
| Human accepts durable guidance | Rolling Ralph Learnings issue |
| Architectural alternatives | Architecture review and issue triage, rather than every coding run |

A new background drift reviewer would need a demonstrated gap in these systems. It should not
duplicate fresh orientation data or create another unowned report stream.

## 13. Net assessment

Ralph already exceeds EGM in deterministic validation, evidence contracts, merge safety, and
unattended operation. EGM contributes three useful additions:

1. a finding lifecycle for both review axes, including recurrence and churn;
2. independent plan assurance before implementation;
3. explicit post-review synthesis and behavioral test cases.

The correct adoption path is incremental instrumentation, executable evaluation, and bounded
experiments. A wholesale review-loop replacement would discard mechanisms that already measured
well.

## Primary Fabro sources

- [Current Ralph graph](../../.fabro/workflows/ralph/workflow.fabro)
- [Planner prompt](../../.fabro/workflows/ralph/prompts/plan.md.j2)
- [Refuter prompt](../../.fabro/workflows/ralph/prompts/refute.md.j2)
- [Standards prompt](../../.fabro/workflows/ralph/prompts/standards.md.j2)
- [Spec prompt](../../.fabro/workflows/ralph/prompts/spec.md.j2)
- [Stage manifest](../../.fabro/workflows/_io/manifest.json)
- [Ralph gate tests](../../ops/gate_tests/test_ralph.py)
- [Ralph invariants](../agents/invariants-ralph.md)
- [Ralph contracts](../ralph/00-overview-and-contracts.md)
- [Ralph trial review](../ralph/review-2026-11-02.md)
- [ADR 0013, merge-gate hardening](../adr/0013-merge-gate-hardening.md)
- [ADR 0016, Stage I/O](../adr/0016-stage-io-server.md)
- [ADR 0029, Ralph workflow trial](../adr/0029-ralph-workflow-trial.md)
- [Factory scorecard proposal](../factory-roadmap/B1-factory-scorecard.md)
