# Planning a new feature with EGM, then into the factory

**Status:** guide, written 2026-11-07. It pairs with the factory planning skill
(`plan-factory-issues`, specified in #67 and not built yet). The skill ships
nothing EGM needs. Until the skill exists, step 5 uses `issue-decomposer` and `issue-reviewer`,
with the factory rules applied by hand.

This guide is for the operator planning a feature that needs design decisions before it can be
cut into factory issues. A bug fix or a small, already-decided change skips EGM. It goes
straight to the planning skill, or to `needs-triage`.

## The idea in one paragraph

EGM (Google's Elephant-Goldfish Model) separates two kinds of context:

- The **Elephant** is your session. It holds the conversation and drafts the design.
- A **Goldfish** is a fresh subagent. It reads only the document and its sources, so it notices
  what the document assumes but never says.

The factory already applies the same principle when it codes: fresh coder sessions, a
cross-family Refuter, and sealed reviewer inputs. What it lacks is the same discipline
*before* an issue exists. EGM provides it for design. The planning skill then turns the
approved design into issues the factory can verify and merge.

```text
idea ─▶ egm-brainstorm ─▶ concepts_brief.md
     ─▶ egm-draft       ─▶ docs/designs/<feature>.md
     ─▶ egm-review      ─▶ findings ─▶ egm-resolve ─▶ (repeat until clean)
     ─▶ egm-review-loop with the factory readiness standard
     ─▶ you approve the design
     ─▶ plan-factory-issues ─▶ published issues (agent / agent-held / agent-split)
     ─▶ factory: precheck ─▶ (sharpening loop if unverifiable) ─▶ ralph ─▶ PR
```

## When to use it

| Use EGM first | Skip EGM |
|---|---|
| A new capability with real alternatives (data model, API shape, where it lives) | A bug with a known cause |
| Work that will become two or more Child issues | A one-file change, or `fast-path` work |
| A change to a public contract, a migration, auth, or concurrency | A dependency or tooling bump |
| You cannot yet write the non-goals | You can already write every acceptance criterion |

The cost is your Claude Code session and its subagents. None of it is factory hosted spend:
the factory's glm, OpenAI and OpenRouter budgets are not touched.

## 0. Install once

```sh
claude plugin marketplace add google/elephant-goldfish-model
claude plugin install egm@egm
```

Start a new session afterwards. In Claude Code the commands are prefixed `/egm:`. EGM's
Claude subagents run with `omitClaudeMd: true`, so a Goldfish does not read the target
repository's `CLAUDE.md`. That is the isolation you want. It also means a Goldfish knows
only the project facts the document states or links to. Write the factory facts into the
design.

Work **inside the target repository** (for example `lawncare-saas`), not in
`fabro-workflows`. EGM resolves linked files against the working directory, and its
codebase reviewer reads that repository's code.

## 1. Brainstorm, with the factory's constraints in the hypothesis

```text
/egm:egm-brainstorm <one paragraph: the feature and why now>
```

EGM asks open questions, then asks you to confirm a two- or three-sentence hypothesis. Put
the factory's constraints into that hypothesis, so that the seven brainstorm lenses test
against them:

- "It will be built by an unattended pipeline. Every acceptance criterion must be provable
  from the code, the diff or `.fabro/ci.sh`."
- "Work must split into slices that each fit one automated run, ordered as a simple chain
  at most."
- Any hard limits: no new external service, no secret, no `.github/workflows/` change.

Read the **zoom-out** (contrarian and reuse) lens's findings most carefully. "Does this
need to exist, and can existing code do it?" is the cheapest point to drop an idea. Save
the result as `concepts_brief.md`, or abandon the idea here.

## 2. Draft the design

```text
/egm:egm-draft
```

EGM writes `docs/designs/<feature>.md` section by section and stops at each one for your
approval. Factory-specific guidance per section:

| EGM section | What to make sure it says |
|---|---|
| Problem, goals | The user-visible outcome. These become the issues' summaries. |
| **Non-goals** | Explicit and specific. They become every issue's `## Non-goals`, and the Contradiction check runs against them. |
| Architecture, alternatives | The chosen option and why the others lost. This becomes the decision ledger, so coders never re-decide it. |
| Risks | Anything that can only be checked after deploy. It becomes `## Post-deploy checks`, not acceptance criteria. |
| `## Detailed Implementation Plan` | Phases that each leave `main` green, and the files each phase touches. |

Two EGM rules need adjusting for this factory:

1. **The file list is a hint, not a boundary.** EGM's readiness standard asks for an exact
   file allowlist. Ralph deliberately lets a coder edit callers, fixtures and generated
   types the plan missed. Keep the list ("expected files"), and do not ask reviewers to
   enforce it.
2. **Phases become issues, not one run.** Each phase in the detailed plan should be a
   candidate tracer-bullet slice: one issue, one Ralph run. If a phase needs more than about
   a dozen tasks, split it now.

## 3. Review with Goldfish

```text
/egm:egm-review docs/designs/<feature>.md
```

Choose the **interactive** preset for a first design, or **uninterrupted** when you want one
consolidated report. Give it a reading list (`GoldfishContext`): the files the design changes,
the repository's `CONTEXT.md` and relevant ADRs, and `.fabro/ci.sh`. Every Goldfish then
sees what the factory will actually run.

What each stage contributes here:

| Stage | Why it matters for the factory |
|---|---|
| Initial and intermediate zoom-out | Removes speculative scope before it becomes extra issues and extra runs |
| Comprehension (paraphrase first) | A wrong paraphrase predicts a misread issue and a `not_met` Refuter verdict |
| Critic and structural | Failure modes and recovery: the parts coders otherwise invent |
| Implementation readiness and codebase | Wrong paths, symbols and current-state claims, caught before they reach a Task list |

Work the findings with `/egm:egm-resolve`. Contest a finding only with evidence the reviewer
could have read: a file, a symbol, a decision in the document. Never contest a comprehension
gap. If a fresh reader misunderstood it, so will a fresh coder.

EGM's own convergence rule is two consecutive clean rounds, capped at five. For a factory
feature, one clean round followed by step 4 is usually enough.

## 4. One extra pass with the factory readiness standard

`egm-review` fixes the standard each stage uses, so the factory's rules go in through
`egm-review-loop`, which accepts a custom standard path. Run it on the detailed plan only:

```text
/egm:egm-review-loop
  TargetFile:      docs/designs/<feature>.md
  SemanticTarget:  the "## Detailed Implementation Plan" section through the end of the file
  Reviewers:       [implementation_readiness, codebase_details, comprehension]
  Standards:       implementation_readiness → <absolute path to this checkout>/docs/egm-research/factory-readiness-standard.md
                   codebase_details         → <same path>
  GoldfishContext: [.fabro/ci.sh, CONTEXT.md, the files the plan names]
```

The standard ([`factory-readiness-standard.md`](factory-readiness-standard.md)) replaces EGM's
zero-decision file allowlist with the factory's rules:

- verifiable criteria;
- `ci.sh`-only command criteria;
- post-deploy checks;
- chain-only ordering;
- one-run slices;
- expected files as a hint.

## 5. Approve, then plan into the factory

Approve the design yourself, or send it as a PR in the target repository if someone else
should read it. Keeping it at `docs/designs/` lets later work link to it. Then:

```text
/plan-factory-issues docs/designs/<feature>.md
```

How the skill uses the design:

| From the design | Into each issue |
|---|---|
| Goals, the phase's purpose | Summary and `## Requirements` |
| Non-goals | `## Non-goals`, copied verbatim |
| Decisions and rejected alternatives | Recorded in the decision ledger; the relevant ones go in `## Context` |
| Current-state claims, verified | `## Context`, with `path:line` |
| Phase steps | Optional `## Task map` hint |
| Phase tests | `## Test expectations` and acceptance criteria the Refuter can check |
| Risks only checkable after deploy | `## Post-deploy checks` |
| Phase order | A Split parent with ordered Child issues (one predecessor each) |

The skill lints every draft and runs its own fresh reviews: a starved implementer, a grounded
audit and the Refuter's-eye pass. It then asks you to approve the graph, and publishes only
after your explicit yes.

**Until the skill exists:**

1. Run `/issue-decomposer` on the design and `/issue-reviewer` on the drafts.
2. Check every criterion against the USER-GUIDE section *Writing criteria the factory can
   verify*, with `.fabro/ci.sh` open.
3. Create a Split parent (`agent-split`) and its Children by hand, using the
   `<!-- fabro:split-child parent=<P> after=<N> -->` first line, `agent` on the first child and
   `agent-held` on the rest.

## 6. After publishing

Nothing more is needed from you until PRs appear.

- The **issue precheck** (local, background) checks each issue against `main`.
- An `unverifiable` verdict starts the **issue sharpening loop** (#68, specified, not built). It
  posts a proposed rewrite for you to approve.
- Ralph plans, codes, refutes and opens the PR. The scheduler promotes each held Child when
  its predecessor closes.

When a feature's design document and the code drift apart later, EGM's `egm-drift-report`
can compare them. The factory's daily repository orientation keeps its own map current
regardless, so run a drift report only when you plan to build on the design document again.

## What not to bring from EGM into the factory

- EGM's human checkpoints stay in *your* session. They never go inside a factory run.
- The Elephant's right to CONTEST a finding with evidence is fine for your design document,
  and is never extended to the factory's Refuter.
- The TRIZ inventive reviewer's suggestions are advisory. Do not turn them into issues unless
  you chose them.

## Sources

- EGM at commit `e9e1f06`: `skills/egm-review/SKILL.md`, `skills/egm-review-loop/SKILL.md`,
  `resources/document_standards/implementation-readiness-standard.md`, `docs/claude.md`.
- This repository: [the research](README.md), [the Ralph comparison](02-ralph-comparison.md),
  `docs/USER-GUIDE.md` (*Writing criteria the factory can verify*, the `fast-path` section),
  and `CONTEXT.md` (Split parent, Child issue, Contradiction, Substitution, Command criterion).
