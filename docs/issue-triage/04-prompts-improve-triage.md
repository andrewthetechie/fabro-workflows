# Task 04 — The two prompts and their contracts

**Depends on:** 00. **Blocks:** 03, 08. **LLM level:** smarter model recommended — these
two files are where triage quality actually lives.

Two prompt files under `.fabro/workflows/issue-triage/prompts/`. They carry the
substance of the `issue-improver` and `issue-triage` skills that the
`issue-triage-loop` relay invokes, rewritten for this deployment's boundaries. Neither
prompt uses a template variable: every input is a file (finding 2), and the existing
prompts in this repository use none either.

Match the house voice in `backlog/prompts/improve.md.j2` — inputs, boundaries, decision
rules, then the output contract — and keep both under the length of `decompose.md.j2`.

## `improve.md.j2`

**Job.** Ratchet one issue toward implementation readiness. Preserve the reporter's
intent, replace uncertainty with repository evidence, make the largest safe improvement
the context supports. A useful partial improvement is a successful run.

**Inputs.** `/tmp/fabro/issue.json` — `.body` is the report, `.title` the current title,
`.comments` the human discussion, `.labels` what the repository already believes. The
checkout in the working directory, read-only, at its current state.

**Boundaries — state all of these.**

- Inspect only. Do not edit tracked files, commit, push, change branches, install
  dependencies, or call `gh`. **The workflow applies your output; you do not.**
- Read-only searches, history and diff inspection, and narrow disposable probes are
  allowed when they establish evidence.
- Preserve original logs, screenshots, reproduction steps and reporter observations.
  Distinguish them from diagnoses and from newly verified facts.
- Label unsupported or ambiguous claims as uncertainty or as an open question. Never
  invent an assumption.
- Correct a claim the repository contradicts rather than preserving confident
  misinformation.
- Cite repository evidence as `path:line`, and paste the smallest exact signature,
  literal or excerpt an implementer must rely on.
- Ask no questions. The next stage owns questions.

**Decision rules.** `improved` when the body changed; `unchanged` when the issue was
already at the checklist's bar and needed nothing. `unchanged` is a legitimate,
successful outcome — a run that rewrites a good issue to look busy is a regression.

**Output contract.** Write `/tmp/fabro/improve.json` and nothing else:

```json
{
  "status": "improved | unchanged",
  "title": "a clearer plain-English title, or the original unchanged",
  "body": "the complete revised issue body in Markdown",
  "reason": "one or two sentences on what changed and why, or why nothing did"
}
```

`body` must be the **whole** body, not a diff or a patch — `improve_gate` writes it
verbatim over the issue. `title` is recorded but **not applied here**; the Conventional
Commits title is written once, by the terminal nodes, from the triage contract.

## `triage.md.j2`

**Job.** Decide whether this issue can be handed to an implementer now, and compile
every implementation-blocking question into one complete batch.

**Inputs.** `/tmp/fabro/issue.json` (refreshed after the improve stage, so `.body` is the
improved body), `/tmp/fabro/human_answer.md` **when it exists** — a human's answers to a
previous batch — and the checkout.

**Boundaries.** The same read-only rules as above, plus the skill's own: *"Never pause to
interview. Emit exactly one final artifact after the analysis is complete. Never emit
partial findings or multiple rounds of questions during one run."* If
`human_answer.md` exists, treat it as evidence, remove the questions it answers, and
compile only what is **still** open — never re-ask a resolved question.

**Classification.** `bug` when documented or intended behaviour is wrong, regressed or
errors unexpectedly. `enhancement` when the request adds capability or deliberately
changes intended behaviour. `mixed` when separable claims exist — recommend splitting.
When intent is unclear, pick the likeliest and name what would change it.

**Readiness.**

- `ready` — the issue plus the repository give an implementer the affected user and
  workflow, current and desired behaviour, scope and boundaries, the domain rules that
  materially affect behaviour, and verifiable acceptance criteria. No blocking question
  remains. Actionable does **not** require file-by-file instructions or a chosen design.
- `needs_info` — at least one open question blocks the work.
- `not_actionable` — evidence shows a duplicate, an already-implemented request, an
  invalid bug, or a previously rejected one.

**Writing questions.** Search the issue history, docs, code, tests and configuration
before asking. Each question asks one concrete decision or missing fact, explains why
the answer blocks or changes the work, and carries an evidence-backed recommended
default whenever one is supportable. Include foreseeable conditional follow-ups in the
same batch. Never ask a question the repository answers, and never ask the reporter to
make an ordinary implementation decision — naming, file placement and component choice
belong to the implementer.

**The title.** Conventional Commits, from the ten types CI actually accepts:
`feat fix docs chore refactor test ci build perf revert`. **Never `!`, never a
`BREAKING CHANGE:` footer** — `release-please` runs on two of these repositories and an
exclamation mark cuts a major version. Scope is optional and lowercase.

**Output contract.** Write **two** files:

`/tmp/fabro/triage.json`:

```json
{
  "readiness": "ready | needs_info | not_actionable",
  "classification": "bug | enhancement | mixed",
  "confidence": "high | medium | low",
  "title": "fix(scope): imperative description",
  "labels": ["bug"],
  "questions": [
    { "id": "Q1", "question": "one concrete decision", "why": "what it blocks", "recommended": "an evidence-backed default, or empty" }
  ],
  "summary": "one sentence for the operator"
}
```

`/tmp/fabro/triage.md` — the issue-ready Markdown artifact posted verbatim as a comment:

```markdown
> *This was generated by AI during autonomous issue triage.*

## Triage
**Classification:** bug | enhancement | mixed (confidence)
**Readiness:** ready | needs-info | not-actionable

### What I established
### Validation and diagnosis | Implementation outline
### Acceptance boundary
### Questions that must be answered
### Recommended next action
```

Constraints `triage_gate` enforces, so the prompt must state them:

- `labels` match `^[a-z0-9:._-]+$` — they are expanded unquoted into a `gh issue edit`
  command line, so a label containing a space breaks the call. Use labels that exist or
  that the workflow creates; `bug` and `enhancement` exist in all four repositories.
- `questions` is non-empty **if and only if** `readiness` is `needs_info`.
- For `ready`, write `None — this issue is workable as written` under the questions
  heading and state that implementation can begin.
- `triage.md` is never empty.

Two invalid attempts and the run releases the claim and stops. The gate's stderr is the
only feedback the retry gets, so it names the contract, not the symptom.

## Acceptance

- Both files exist, are referenced as `prompt="@prompts/<name>.md.j2"`, and `fabro
  validate` resolves them.
- Neither contains `{{` or `{%`. A template variable that does not exist renders empty
  and records `template_undefined_variable` — a warning offline, an **error** at run
  creation.
- A dry read-through as a context-starved implementer: given only the contract sections,
  it is unambiguous which file to write, what shape, and where.
- The ten Conventional Commits types in the prompt match the ten in `triage_gate`'s
  regex exactly. They are two copies of one fact; a drift between them fails every run
  at the gate.
