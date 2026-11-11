# The factory issue format

What a draft must be for the factory to implement, prove and merge it with no human. The lint
checks the mechanical part of each rule; its id is in brackets. The judgment part is yours.

## The sandbox

An issue is planned once by Ralph, then coded Task by Task by fresh sessions that share nothing
but `progress.md`. Each Iteration writes inside the checkout and stops: the pipeline owns commits,
branches and publication, deterministic stages own `gh` and GitHub, and `ci.sh` is the only test
runner anyone reads. There is no sub-agent to delegate to, a Task gets a 60-minute timeout, and a
run gets 8 Tasks. The **Refuter** then sees only the issue, the diff and `ci.sh`'s run, and is told
to prove the work is not done.

Write the issue a stranger can finish: every name with its shape, every pattern with the excerpt to
copy, every check as a command `ci.sh` runs. What needs a credential, a deployed environment, a
GitHub setting or a workflow file is a human action or a post-deploy check.

## The factory issue format

```markdown
# <type>(<scope>): <what changes>

<One to three sentences: the outcome, or a user story.>

## Context

Verified facts at `main` (<date>). Proposals are under *Requirements*.

- `<path>:<line>` <the fact, with the smallest excerpt the implementer relies on>

## Requirements

1. <what must be true when this lands>

## Acceptance criteria

- [ ] <one observable state the Refuter can mark met>

## Non-goals

- <what this issue leaves alone>

## Test expectations

- <runner, file, the case, a literal input and its expected output>

## Expected files            (optional: a hint, never an allowlist)
## Task map                  (optional: an ordered hint for Ralph's plan)
## Human actions             (optional: what the operator does; never the agent's work)
## Post-deploy checks        (optional: `- [ ] ` items only a human can check after merge)
```

- **Title** [L7]: Conventional Commits, as the merge gate reads it: `feat`, `fix`, `docs`, `chore`,
  `refactor`, `test`, `ci`, `build`, `perf` or `revert`, an optional lower-case scope, an optional
  `!`. There is no `style`.
- **Sections** [L6]: Context, Requirements, Acceptance criteria, Non-goals and Test expectations are
  required, each once. A Split parent needs only the title and the summary.
- **Markers** [L8]: a draft carries no `<!-- fabro:... -->` line and no "Blocked by #N". The
  publisher writes the one marker a Child issue gets, from the plan's `after`.

## Context: facts, pasted

Context holds facts you read in this session, each with `path:line`, apart from proposals and open
questions. Paste what the implementer builds against — a signature, a type, a config key, the
excerpt of a pattern to copy: a name alone makes a context-starved coder invent the shape. A fact
you could not verify is a gap for the Grill, or a verification step in the issue.

## Criteria the Refuter can verify

A criterion is an observable state of the code, the tests, or `ci.sh`'s output. Source:
`docs/USER-GUIDE.md`, *Writing criteria the factory can verify*.

- **Behavioural**: one trigger and one observable response, on EARS's "When X, the Y shall Z".
  "`GET /invoices/7/export.csv` by a user who cannot see invoice 7 returns 404."
- **Command criterion** [L2]: a command `ci.sh` runs, directly or through what it calls, with the
  route cited: "`uv run pytest -q tests/` passes (`.fabro/ci.sh:7`)". `$PLAN facts` lists the routes.
  A command `ci.sh` never runs is rewritten as a behaviour, or adding it to `ci.sh` becomes a human
  action. GitHub's checks never count: the Refuter sees `ci.sh`'s run, not GitHub's.
- **Text in the repository, never in the PR** [L1]: "the PR explains why X is unreachable" becomes
  "a code comment at the line says why X is unreachable". Before/after numbers go in Context, or
  are dropped.
- **A raised threshold, never "must not drop"** [L3]: "`fail_under` in `pyproject.toml` is raised
  to the new total, rounded down". `ci.sh` enforces it and the diff shows it.
- **Human-only checks under `## Post-deploy checks`** [L5, L6]: a screenshot, a manual or visual
  check, another operating system, a deployed environment, real credentials. Write the heading
  exactly `## Post-deploy checks` and each item as `- [ ] `: the pipeline cuts that heading from
  the Refuter's input and copies those items into the PR body.
- **`.github/workflows/` belongs under `## Human actions`** [L4]: the agent's token cannot push a
  workflow file, so the run would stop before its PR.

**Contradiction**: no criterion may need a surface (a backend, an external API, a secret, a
migration) that a non-goal of the same issue excludes. Only the operator resolves one, by changing
either side; left in, the precheck reports `contradiction` and Ralph's `plan` returns the issue to
triage.

## Expected files and the Task map

Both are hints, and Ralph's `plan` decides for itself. Phrase expected files as "expected", each
with its reason: the coder may also edit callers, fixtures, generated types and tests the change
needs, and a reviewer must not call that scope creep. A Task map is an ordered list of steps, each
with its files and the requirements it covers.

## Slices, order and lanes

**A slice is a tracer bullet**: a narrow, complete path through every layer it touches, leaving
`main` building and green. "Add the schema", "add the API", "add the UI" is three halves of
nothing. A **prefactor** is its own slice, first, when it removes a named obstacle.

**A slice fits one Ralph run** [L9]: at most 4 Task map steps and 6 expected files. Past that,
measured runs auto-merged half as often and ended without a PR twice as often
(`scripts/measure_sizes.py`). Split it before publishing; Ralph's 8-Task budget files the rest as a
Remainder issue, and a 60-minute Iteration timeout ends a Task too large to finish.

**Order** maps onto what the scheduler holds (ADR 0015 D4):

| The dependency | In `plan.json` |
|---|---|
| None | A separate issue: `{"key": "a", "lane": "agent"}`. Independent issues run in parallel on different boxes. |
| A chain | A **Split parent** (`{"key": "p", "role": "parent"}`) and **Child issues** listed in order, each with `"parent": "p"`; every child after the first names one predecessor with `"after": "<key>"`. The publisher labels the first child `agent` and each later one `agent-held`; the scheduler promotes it when its predecessor closes completed. |
| Two predecessors | Not expressible. Merge the predecessors, re-order them into one chain, or hold the slice for the operator [L8]. |
| Two parallel slices that expect the same file | A collision when both run at once [L11]. Chain, merge, or waive. |

A wide refactor that cannot stay green slice by slice is an expand–migrate–contract chain: add the
new form beside the old, move callers in batches, then remove the old form last.

**Lanes**, one per issue, confirmed in the Quiz:

| Lane | When |
|---|---|
| `agent` | Ready: every decision made, every criterion verifiable. |
| `fast-path` | Also one commit: one Task map step at most, under 200 changed lines and 10 files [L10]. It skips `decompose`, `plan` and the extra review, and merges from the queue on green. |
| `needs-triage` | The operator left a question open. Triage asks it. The `issue-triage` schedules are off, so `arch-review` or a hand-fired triage picks it up. Never on a Child issue. |

Add `priority` only when the operator asks. Other labels go in the entry's `labels`.

## From an EGM design

| From the design | Into each issue |
|---|---|
| Goals, the phase's purpose | The summary and `## Requirements` |
| Non-goals | `## Non-goals`, verbatim |
| Decisions and rejected alternatives | The decision ledger; the relevant ones in `## Context` |
| Current-state claims, verified | `## Context`, with `path:line` |
| Phase steps | `## Task map` |
| Phase tests | `## Test expectations`, and criteria the Refuter can check |
| Risks only checkable after deploy | `## Post-deploy checks` |
| Phase order | A Split parent with Child issues, one predecessor each |

## Waivers

A finding the operator accepts goes in the issue's plan entry, with the reason:
`"waive": {"L9": "five of the seven files are fixtures"}`. L6, L7 and L8 cannot be waived: each one
breaks the pipeline, not a heuristic.
