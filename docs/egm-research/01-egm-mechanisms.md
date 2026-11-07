# EGM mechanisms

**Source version:** `e9e1f06aa4e26e95cd4ed37210a0632202c429e9`. This document describes the
executable prompts and their supporting records. It distinguishes specified behavior from tested
behavior.

## 1. System model

EGM divides work between two context types:

- The **Elephant** holds the conversation, project history, requirements, and earlier decisions.
  It brainstorms, drafts, evaluates findings, and applies approved document edits.
- A **Goldfish** receives a fresh subagent context. It reads the assigned document, its persona,
  an optional standard, and an explicit reading list. It does not receive the earlier conversation.

The skills refuse to call a same-context review a Goldfish review. This makes fresh delegation a
correctness requirement, not an implementation preference.

The main flow is:

```text
brainstorm -> concepts_brief.md -> draft -> design_draft.md
          -> staged fresh reviews -> findings -> Elephant resolution
          -> human-approved design -> implementation outside EGM
          -> later drift report
```

EGM is primarily a design and document-review plugin. It does not implement the proposed code,
run a coding loop, or merge a pull request. Comparisons with Ralph must account for that boundary.

## 2. Upstream ideation and drafting

### 2.1 Brainstorm

`egm-brainstorm` begins with open-ended questions across multiple turns. It probes the problem,
constraints, current behavior, non-goals, and success measures. The user must confirm a two or
three sentence hypothesis before reviewer delegation starts.

The synthesizer then requests seven lenses:

| Lens | Persona used | Main question |
| --- | --- | --- |
| Contrarian and reuse | `zoom_out` | Is the feature necessary, and can existing code provide it? |
| Architecture and cost | `design_critic` | What resource, scaling, quota, or failure limits apply? |
| User experience | `comprehension` | Does the interface create avoidable cognitive friction? |
| Migration | `implementation_readiness` | How do compatibility, rollout, and rollback work? |
| Operations | `codebase_details` | How will an operator trace and debug the behavior? |
| Maintenance | `structural` | What coupling or long-term maintenance cost follows? |
| Invention | `inventive_catalyst` | Is the design accepting a false compromise? |

The output is `concepts_brief.md`, with goals, non-goals, alternatives, analysis, and decisions.
The user can refine the hypothesis, abandon it, publish the brief, or continue to drafting.

The useful factory idea is early rejection of a weak premise. The interactive interview itself
does not translate to Ralph, which deliberately has no waiting human gate.

### 2.2 Draft

`egm-draft` writes a design incrementally. Each major section has a human approval checkpoint.
The draft covers the problem, goals, architecture, alternatives, and detailed implementation.
It reconciles the result against the concept brief and asks the user to resolve contradictions.

When several architectural answers are defensible, the skill presents up to three options with
mechanisms, trade-offs, and failure conditions. The author chooses. Mechanical choices with one
defensible answer remain with the agent.

The detailed plan must enumerate every file to create, modify, or delete, with a rationale. It
must not contain implementation code. Schemas, type definitions, and pseudocode are allowed.
The architecture record describes the resulting implementation write boundary as a convention,
not an enforced allowlist.

The semantic split is important:

- `FeatureRange` covers the high-level design.
- `DetailedRange` starts at `## Detailed Implementation Plan` and covers execution.

Reviewers can then judge architecture and actionability without mixing those concerns. Fabro can
implement the same boundary with separate Stage inputs rather than Markdown line-range discovery.

## 3. The five-stage review pipeline

`egm-review` offers interactive, uninterrupted, feature-only, and implementation-only presets.
The full sequence is:

| Stage | Target | Reviewers | Purpose |
| --- | --- | --- | --- |
| 5, initial strategic review | Full document | `zoom_out`, `inventive_catalyst` | Check root problem, simplicity, reuse, and false compromises |
| 6, comprehension | Both semantic ranges | `comprehension` | Test whether a fresh reader understands the document |
| 7, critic | `FeatureRange` | `design_critic`, `structural`, `style` | Test architecture, risks, structure, and claim precision |
| 8, intermediate zoom out | Full document | `zoom_out` | Prune complexity added by earlier repairs |
| 9, implementation readiness | `DetailedRange` | `implementation_readiness`, `codebase_details`, `style` | Test actionability and agreement with the real code |

The stages run sequentially. The initial and intermediate zoom-out passes have different jobs.
The first challenges the premise. The second prevents the repair process from adding speculative
complexity.

### 3.1 Strategic review

The `zoom_out` standard requires context sufficiency, root-problem focus, simplicity, appropriate
abstraction altitude, and reuse. The persona must search for existing components when the design
proposes new utilities or modules.

The inventive reviewer is advisory. It looks for technical contradictions and suggests patterns
such as segmentation, inversion, or an intermediary. Every suggestion must include secondary
operational costs. It must answer "No false compromise detected" when no real contradiction
exists. Its findings do not join the mandatory defect-repair matrix.

This separation is sound. An inventive reviewer can propose alternatives without making every
suggestion a required change.

### 3.2 Comprehension

The comprehension reviewer first paraphrases:

- what the document proposes;
- how the existing system works, according to the document.

An inability to paraphrase a section is a Blocker. The reviewer then checks definitions, links,
assumed prerequisites, dependencies, and side effects. It reads the rest of the document before
calling a term undefined. General engineering vocabulary is not a finding.

The orchestrator invokes this reviewer directly, not through the synthesizer. Aggregation would
discard the paraphrase, which is evidence about the reviewer's understanding.

This is the main lesson for plan handoffs. A list of defined terms does not prove that a fresh
implementer understands how the parts connect.

### 3.3 Critic and readiness

The design critic checks standard compliance, unsupported assertions, architectural risks,
scale limits, security, and failure recovery. The structural reviewer checks the required layout.
The style reviewer checks objective claims, filler, and defensive caveats.

The readiness reviewer asks whether an implementer must make any remaining design choice. The
codebase reviewer checks current-state claims, paths, symbols, APIs, configuration, and failure
behavior against source code. It must not report a proposed new file as missing. A file list that
does not distinguish Create, Modify, and Delete receives one Major finding instead.

### 3.4 Standards

The standards are short and operational:

- The feature standard requires goals, non-goals, architecture, alternatives, risks, and concrete
  rejection reasons.
- The readiness standard requires phased stable states, exact file boundaries, actionable steps,
  and specific tests.
- The zoom-out standard requires the simplest root-cause solution that uses existing conventions.
- The re-implementation standard describes current behavior in enough detail to reproduce its
  external contracts. It is not applied to the comprehension stage.
- The Heilmeier standard asks for objectives, current practice, novelty, impact, risks, cost,
  timeline, and measurable exams.

The readiness standard's zero-decision rule is deliberately strict. It fits a human-approved
implementation contract. It can conflict with an autonomous coder that must discover necessary
integration edits.

## 4. Context construction

EGM does not give a reviewer the entire parent conversation. It builds `GoldfishContext` from:

1. an optional user reading list;
2. local files linked by the design document;
3. approved external files or saved copies of web pages.

Only links that resolve inside the working directory join automatically. External paths and URLs
need approval. A headless run omits unapproved external inputs and reports the omission. A URL is
saved as a local file because not every reviewer can open URLs.

Stage 6 receives the document alone. Other stages receive the constructed reading list.

Fresh parent-turn isolation does not prove exclusion of project instructions. EGM's harness guide
records reports of project and user `AGENTS.md` injection on Codex and Antigravity. Its tests treat
parent-turn isolation and instruction-file exclusion as separate checks.

## 5. Synthesis and reviewer coverage

The synthesizer is an intermediate subagent. It can launch two independent instances of a persona
for additional recall, but that A/B sampling is optional. It then:

- deduplicates overlapping findings;
- groups them as Blocker, Major, or Nit;
- normalizes source references;
- writes the report artifact;
- returns only a small summary and artifact path.

A reviewer that fails, times out, or returns no report is retried once. A second failure is
recorded as `not run`. A missing status also means `not run`. The synthesizer must not translate a
failed reviewer into zero findings.

This is a coverage rule, not merely reporting hygiene. A review round cannot be clean when a
requested reviewer did not run.

EGM's nested synthesizer is not required to obtain these benefits. A deterministic orchestrator
can collect independent results and enforce the same coverage rule.

## 6. Convergence and finding state

The review loop specifies a richer state model than "repeat until no findings":

- **Settled:** contested or recorded as accepted risk. A later reviewer does not re-open it.
- **Fixed but not settled:** an edit was applied. A later occurrence re-opens the finding because
  the fix failed or a later edit reintroduced the issue.
- **Clean round:** no unsettled Blocker or Major finding, and every requested reviewer completed.
  Nits do not block. Cleanliness is decided before applying that round's fixes.
- **Converged:** two consecutive clean rounds.
- **Cap:** five rounds.
- **Churn:** a new finding concerns an earlier fix, and reverting that fix would resolve it.

At the cap or on churn, the loop asks the user to accept the remaining findings, continue, or
return to drafting. Individual stages also allow at most one re-audit after a manual update.

Commit `9162c4e` explains why EGM replaced zero-finding convergence: fresh reviewers sample
differently, so zero findings is rare and one clean round does not establish stability. The same
commit added the rule that fixes do not settle findings and that reviewer coverage affects
cleanliness.

The strongest transferable idea is the finding lifecycle. The exact two-clean-round policy is a
cost and risk choice, not a universal requirement.

## 7. Resolution rules

The persistent Elephant classifies each finding:

| Category | Meaning | Required evidence or action |
| --- | --- | --- |
| `PROPOSE FIX` | The finding is valid | Define a precise text or structural repair |
| `CONTEST` | The finding is wrong | Cite a file, symbol, or decision the reviewer could read |
| `ESCALATE` | External knowledge could refute it | Give the decision to the user, without inventing document context |
| `DECLINE` | The Nit is not worth fixing | Record it without an edit |

A contest must be checkable. "This was intentional" is not evidence. The resolver cannot lower a
finding's severity. It must contest the substance instead. It cannot contest a comprehension gap,
because a fresh reader's failure is itself evidence of missing context.

The user then auto-accepts fixes, reviews items individually, comments on the artifact, continues
without edits, or asks an unrelated question. Resolution must yield and wait for the user.

These rules reduce sycophantic repair, but they give the document author permission to reject an
independent finding. Fabro should not copy that permission into its Refuter gate. Ralph can use
`needs_human_review` for evidence that only a human can supply.

## 8. Bootstrap and drift reporting

`egm-bootstrap` generates directory documentation bottom-up. Explorers return public interfaces,
invariants, and side effects. Parent documents combine child contracts. Generated blocks sit
between sentinel markers so later runs preserve human-written text outside those blocks.

`egm-drift-report` later compares checked-in documents with current source. It reports missing
features, incomplete models, logic shortcuts, and architectural divergence. It processes documents
sequentially and reports differences only.

These features address stale context after implementation. Fabro's daily repository orientation
already regenerates a current map. Its learning issue collects proposed rules for human adoption.
A separate drift system would need to add a distinct check, not duplicate those mechanisms.

## 9. What the tests establish

The local offline run completed with **340 passed and 2 skipped**. The suite covers packaging,
frontmatter, links, manifests, adapters, selected prompt invariants, and behavioral scenario file
structure.

The `test_scenarios.yaml` files contain useful cases for convergence, missing reviewer status,
comprehension paraphrases, external context, and false compromises. The ordinary offline suite
does not execute those review behaviors against models. EGM's drift design states this limit
explicitly for its own scenario.

Opt-in native probes test real installations and selected fresh-context behavior on some hosts.
The harness record distinguishes installation, workflow execution, parent-turn isolation, and
instruction-file exclusion. Some cloud surfaces still have incomplete trace evidence.

Therefore:

- The prompts provide a detailed review protocol.
- Static checks establish package integrity and selected textual rules.
- Native probes establish selected runtime behavior on named hosts.
- The repository does not establish reviewer precision, recall, false-block rate, or universal
  isolation across supported hosts.

Fabro should reuse the behavioral ideas, but should test them with executable gates and recorded
model evaluations before changing production routing.

## 10. Transfer assessment

| EGM mechanism | Transfer value for Fabro | Reason |
| --- | --- | --- |
| Finding lifecycle and churn | High | Reviewer history is absent on Ralph's two review axes |
| Reviewer coverage status | Existing equivalent, keep Fabro's stricter form | Ralph already rejects missing or invalid reviewer results |
| Architecture, comprehension, readiness split | High for selected plans | Ralph has no independent plan review |
| Post-review deduplication | Medium | It can reduce duplicate Tasks without weakening independence |
| Two clean rounds | Unproven for Ralph | Successful runs would pay for another full review cycle |
| Contest and accepted-risk state | Reject for the Refuter | It conflicts with the independent merge gate and unattended operation |
| Semantic input ranges | High as a design technique | Stage manifests can enforce cleaner boundaries than line scanning |
| Strict zero-decision planning | Selective only | Necessary integration work can exceed planned file hints |
| Inventive contradiction review | Low and advisory | It can generate complexity and belongs upstream of routine implementation |
| Living-document drift checks | Existing partial equivalent | Repository orientation already refreshes current context |
| Artifact-first contracts | Existing stronger equivalent | Fabro uses schemas, receipts, and deterministic gates |

## Primary sources

- [Review pipeline](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/skills/egm-review/SKILL.md)
- [Review loop and convergence](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/skills/egm-review-loop/SKILL.md)
- [Resolution](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/skills/egm-resolve/SKILL.md)
- [Synthesizer](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/agents/synthesizer.md)
- [Comprehension](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/agents/comprehension.md)
- [Implementation readiness standard](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/resources/document_standards/implementation-readiness-standard.md)
- [Harness limits](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/docs/harnesses.md)
- [Testing record](https://github.com/google/elephant-goldfish-model/blob/e9e1f06aa4e26e95cd4ed37210a0632202c429e9/TESTING.md)
- [Convergence change, commit 9162c4e](https://github.com/google/elephant-goldfish-model/commit/9162c4e)
