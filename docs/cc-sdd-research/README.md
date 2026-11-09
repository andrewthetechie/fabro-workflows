# cc-sdd research for the software factory

**Status:** research complete. The recommendations are not approved or implemented.
**Research date:** 2026-11-09.

## Reader and intended action

This series is for the engineer who changes Ralph, the scheduler's dispatch, or the planning
skill specified in #67. After reading it, that engineer can pick a first shadow measurement
without weakening a merge gate.

The main recommendation is to borrow four mechanisms. Each one starts as a measurement. None
of them changes routing until its data supports the change:

1. Check mechanically that a Task's new tests fail without its implementation.
2. Run a fresh diagnosis when a Task misses twice, and record a root-cause category.
3. Check, before dispatch, whether two queued issues will edit the same files.
4. In `plan_gate`, check that every acceptance criterion has a Task. In the #67 planning skill,
   review the drafted issues together as one set.

The [adoption plan](01-adoption-plan.md) has the contracts, measurements and stop conditions.

## What cc-sdd is

[cc-sdd](https://github.com/gotalab/cc-sdd) (`e2a0c671aef37a482404905b409ab5c27092b25b`,
package version 3.1.0) is an npm installer. It installs 17 Markdown skills into a coding
agent's skill directory. The skills follow Kiro's spec-driven workflow:

```text
kiro-discovery -> brief.md (+ roadmap.md for multi-spec work)
kiro-spec-init -> kiro-spec-requirements -> requirements.md   (EARS criteria, numeric ids)
kiro-spec-design                         -> design.md         (Boundary Commitments, File Structure Plan)
kiro-spec-tasks                          -> tasks.md          (_Boundary:_, _Depends:_, (P) markers)
kiro-impl  -> per Task: implementer subagent -> reviewer subagent -> commit
           -> on BLOCKED or a third rejection: debug subagent -> RETRY | BLOCK | STOP
           -> kiro-validate-impl (feature-level GO / NO-GO)
```

Everything is prompt-specified. No code enforces a step: the installer copies templates, and
the agent's own parent context parses the subagents' Markdown status blocks. A human approves
each phase in `spec.json`, and each run is interactive.

Its stated design idea (`docs/guides/why-cc-sdd.md`) is that a spec is a *contract between
parts of the system*. Boundaries are declared at each phase and checked at each later phase:
discovery names `Boundary Candidates`, design commits to `Boundary Commitments`, tasks carry
`_Boundary:_`, and review rejects `Boundary Violations`.

## Where Ralph already matches or exceeds it

Ralph already does most of what `kiro-impl` describes, and it enforces those rules in command
nodes where cc-sdd uses prompts.

| cc-sdd mechanism | Factory equivalent | Assessment |
| --- | --- | --- |
| A fresh implementer subagent for each Task | Each Iteration is a fresh session with no `thread_id` | Same |
| Implementer status `READY_FOR_REVIEW \| BLOCKED \| NEEDS_CONTEXT`, re-requested once if it does not parse | `submit` validates against a schema, and the Iteration's `blocked` object (`scope`, `criterion`) | Ralph is stronger: schema and receipt, not prose parsing |
| `## Implementation Notes` passed to later Tasks | `learnings` in `progress.md`, and the `learn` stage with evidence filtering | Same, plus a post-run record |
| Parent alone commits, with selective staging and no destructive reset | Git is read-only for agents, and the checkpoint layer owns history | Ralph is stronger |
| Validation commands discovered per run from manifests | `.fabro/setup.sh` and `.fabro/ci.sh` per repository, `task_check` selection | Ralph is stronger |
| A reviewer per Task, then `kiro-validate-impl` per feature | Mechanical Task validation for each Task, then Final validation, the Refuter, standards and spec reviews for the run | Different on purpose (see *Not to import*) |
| Two rejections, then a debug subagent, with two debug rounds at most | The rung ladder (t1 to t4) with `## Last attempt` | Ralph escalates the model, but runs no diagnosis. **Gap 2** |
| Grep for `TBD`, `TODO` and `FIXME` in changed files | `hygiene` counts unlinked `TODO`, `FIXME` and `XXX`, skip markers, tautologies, broad excepts and type escapes | Ralph is stronger |
| RED phase evidence (`RED_PHASE_OUTPUT`), required by the reviewer | `iterate.md.j2` asks for Red-Green-Refactor. Nothing checks it | **Gap 1** |
| Requirements traceability: every numeric id mapped to a task, checked mechanically before `tasks.md` is written | `plan.md.j2` asks that every requirement have an owner. `plan_gate` checks the shape only | **Gap 4a** |
| `(P)` markers and `_Boundary:_` scopes decide which tasks are parallel-safe | The scheduler runs issues in parallel on separate boxes, with no file-overlap check | **Gap 3**. The cost is known: `final_merge`, `resolve_merge` and `resolve_base` exist to repair overlap afterwards |
| Out of Boundary, non-goals | `## Non-goals` in the issue and the `contradiction` disposition | Same |
| Discovery's "no spec needed" path | The `fast-path` lane and the precheck verdicts | Same |
| Steering files kept as project memory | Daily repository orientation and per-issue context packs | Same, and fresher |
| `kiro-spec-batch` writes many specs, then a cross-spec review | #67 drafts many issues, then three fresh reviews of each issue | #67 has no review across the set. **Gap 4b** |
| `UPSTREAM` ownership when a failure belongs to another spec | `baseline_check`, and #24 (red `main`) | Covered by #24 |

## Main findings

### Gap 1. Nothing checks that a Task's tests prove its behaviour

cc-sdd treats "the test would fail if the implementation were removed" as its central review
check. Its implementer must capture the failing output before writing code, and its reviewer
rejects a task without that output. For behavioural tasks, its Feature Flag Protocol adds a
flag, so that the new test compiles and fails for a behavioural reason, not for a missing
symbol.

Ralph's Iteration prompt asks for the same Red-Green-Refactor cycle, but only as advice.
`hygiene` catches tautologies and skip markers. It cannot catch a test that is real but passes
whether or not the feature exists. Today that test reaches the Refuter as evidence that a
criterion is met.

A command node can measure this. Reverse the Task's non-test diff, run the Task's new tests,
then re-apply the diff and confirm the tree is unchanged. No agent needs to report anything,
and the result cannot be faked.

### Gap 2. A Task that misses twice gets a stronger model, not a diagnosis

The cc-sdd debugger runs in a fresh context. It returns a `ROOT_CAUSE`, one of ten categories
and a `NEXT_ACTION`. The useful part is that three categories are not code problems:
`TASK_ORDERING_PROBLEM`, `TASK_DECOMPOSITION_PROBLEM` and `SPEC_CONFLICT`. For those, the
debugger stops the loop instead of trying another patch ("Do not propose a brute-force code
fix as a substitute for revising `tasks.md`").

Ralph's ladder gives the next rung the previous attempt's failure, on a larger model. It never
asks whether the Task itself is wrong, so a badly cut Task can use all four rungs. The open
retro issues show what late rungs cost: #73 is 14.5 box-hours in one `iterate_t3` that was
terminated.

A categorised diagnosis would also give the retro a reason that `failure_signature` cannot
give for a run that stopped at `mark_stuck`.

### Gap 3. The scheduler dispatches overlapping issues together

cc-sdd marks a task `(P)` only when its `_Boundary:_` does not overlap any pending task's. The
factory has the same problem across issues, and it has no rule. Two sibling issues that touch
the same lines run at the same time. One of them then pays for `final_merge`, `resolve_merge`,
`resolve_base` or a merge-queue rebase. The memory of the 2026-10-12 final-merge fix records
two such conflicts on womens-fantasy-sports (#1834 and #1747). It also records that the cause
was never fixed.

The data to predict overlap already exists. The context pack posts a `### Files` list for each
queued issue, with every path checked against the checkout. #67's issue format adds an
expected-files list.

### Gap 4. Coverage is checked by a prompt, and sibling issues are reviewed one at a time

**4a.** cc-sdd runs mechanical checks before judgement in each review gate. Its first check
extracts every requirement id and reports any that no task names. Ralph's planner is told to
quote the requirement in `covers`, but `plan_gate` never compares `covers` with the issue's
`## Acceptance criteria`. A criterion with no Task reaches the Refuter as `not_met`, after a
full loop. The comparison is a string match in a command node. It can run before #69's
LLM plan reviews, and it costs nothing.

**4b.** `kiro-spec-batch` ends with one fresh reviewer that reads every new spec together. It
checks data-model consistency, producer and consumer contract alignment, capabilities that two
specs both claim, and files that two specs both claim. cc-sdd calls this "the highest-value
quality gate". #67's three Goldfish reviews each read one issue. Nothing reads the set, and the
set is what the scheduler will run in parallel.

## Not to import

- **An LLM reviewer after each Task.** Ralph validates each Task mechanically and reviews the
  whole run once. The in-run pre-review measurement
  (`docs/plans/more-local-inference/09-in-run-pre-review.md`) explains why a per-Task review
  was not worth its box time.
- **Human approval at each phase.** Ralph is unattended. The operator approves upstream, when
  the issue is written (#67, *Quiz Graph*).
- **Three spec documents for each issue.** The issue plus Ralph's Task list is the contract.
  This matches the EGM research's conclusion.
- **The Feature Flag Protocol as a coding rule.** Adding and then removing a flag in every
  behavioural Task adds diff churn. The Gap 1 probe gets the same evidence from the finished
  diff.
- **`BLOCK_TASK`, which skips a blocked task and continues.** In the factory, the PR would
  miss a criterion, and the Refuter would hold it. A `scope` block already ends the loop, and
  the Remainder issue carries leftover work.
- **A per-Task file allowlist (`_Boundary:_` as a limit).** The factory readiness standard
  forbids it, because integration edits are legitimate. Gap 3 uses predicted files only to
  order dispatch. It never limits what a run may edit.
- **The "Common Rationalizations" tables** in cc-sdd's prompts. They are a prompt technique
  that could help or hurt. Test them with the EGM reviewer evaluation harness (EGM work
  item B) before using them, not before.

## Considered and deferred

- **EARS phrasing for acceptance criteria** ("When X, the Y shall Z"). The factory's
  `unverifiable` holds come from criteria that `ci.sh` cannot check, not from grammar. #67
  could cite EARS as a style for behavioural criteria. A lint rule would not address the
  measured cause.
- **A runtime smoke check after Final validation.** cc-sdd refuses `FEATURE_GO` without a
  boot check, because type-only imports, module format and boot-time configuration can pass
  every test and still fail at start-up. A repository can already put a boot check in
  `ci.sh`. Revisit this if post-merge reverts or hotfix issues show start-up failures.

## Sources

- cc-sdd at `e2a0c67`: `tools/cc-sdd/templates/agents/claude-code-skills/skills/`
  (`kiro-impl` and its `templates/`, `kiro-review`, `kiro-debug`, `kiro-verify-completion`,
  `kiro-validate-impl`, `kiro-discovery`, `kiro-spec-batch`), `templates/shared/settings/rules/`
  (`tasks-generation.md`, `tasks-parallel-analysis.md`, `design-review-gate.md`,
  `requirements-review-gate.md`, `ears-format.md`, `steering-principles.md`), and
  `docs/guides/why-cc-sdd.md`.
- [Ralph graph](../../.fabro/workflows/ralph/workflow.fabro),
  [Iteration prompt](../../.fabro/workflows/ralph/prompts/iterate.md.j2),
  [planner prompt](../../.fabro/workflows/ralph/prompts/plan.md.j2),
  [Ralph invariants](../agents/invariants-ralph.md).
- [Context pack](../../.fabro/workflows/context-pack/workflow.fabro) and the scheduler's
  [`pack.py`](../../ops/scheduler/src/fabro_scheduler/pack.py).
- [EGM research](../egm-research/README.md), which this series builds on and does not
  repeat: reviewer history (#70), plan review (#69), the evaluation harness, and #67.
