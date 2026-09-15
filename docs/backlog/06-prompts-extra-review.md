# Task 06 — Prompts: `standards.md.j2`, `spec.md.j2`, `quality.md.j2`, `extra_decompose.md.j2`

**Depends on:** nothing. **Do before:** task 08.
**LLM level:** local is fine.
**Read first:** task 03 §"Rules that apply to every prompt file in tasks 03–06".

## Goal

Write the four prompts for the **extra review** phase — the whole-branch review that
runs after every decomposed task is integrated, before the PR is opened. Three
independent reviewers each write a findings file; a fourth agent merges those findings
into follow-up tasks that get appended to the task list.

Write exactly four files under
`~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog/prompts/`:

- `standards.md.j2`
- `spec.md.j2`
- `quality.md.j2`
- `extra_decompose.md.j2`

All four are new. This phase runs at most **2 rounds** (`extra_round`, capped in
`extra_prep`).

## Shared inputs for the three reviewers

All three reviewers are **read-only whole-branch** reviewers. They see the accumulated
work of the entire run, not one task. The prompt must say so: the branch may contain
several tasks' worth of change, and the reviewer judges the branch as a unit.

| Path | What it is |
|---|---|
| `/tmp/fabro/extra/diff.patch` | `git diff origin/main...HEAD` — everything this run changed |
| `/tmp/fabro/extra/diffstat.txt` | `git diff --stat origin/main...HEAD` |
| `/tmp/fabro/extra/changed_files.txt` | `git diff --name-only origin/main...HEAD` |
| `/tmp/fabro/issue.json` | the parent issue — reporter intent and acceptance criteria |
| `/tmp/fabro/completed.md` | per-task completion notes appended by `integrate`; what the run believes it did |

The repository checkout is available **read-only**. No edits, no commits, no GitHub,
no test runs — `./.fabro/ci.sh` already passed on this branch.

Say explicitly that there is **no PRD document** here (the Sandcastle sources assume
one): the parent issue in `/tmp/fabro/issue.json` plays that role.

## Shared output contract for the three reviewers

Each reviewer writes **one** file, raw JSON, no markdown fence:

```json
{
  "decision": "approve" | "findings" | "needs_human_review",
  "summary": "one or two sentences stating the verdict and why",
  "findings": [
    {
      "message": "what is wrong and what the follow-up work must change",
      "files": ["path/to/file.ts"],
      "suggestion": "the concrete remediation"
    }
  ]
}
```

| Node | Prompt | Writes to | Class / model |
|---|---|---|---|
| `standards` | `standards.md.j2` | `/tmp/fabro/extra/standards.json` | `.review` → `glm-5.3` |
| `spec` | `spec.md.j2` | `/tmp/fabro/extra/spec.json` | `.review-frontier` → `kimi-k3` |
| `quality` | `quality.md.j2` | `/tmp/fabro/extra/quality.json` | `.review` → `glm-5.3` |

Rules every reviewer prompt must state:

- `approve` — nothing worth a follow-up task. `findings` is `[]`.
- `findings` — at least one follow-up is warranted; `findings` must be non-empty.
- `needs_human_review` — the branch cannot be judged safely. **This routes the whole
  run to the human rescue gate**, so use it only when a human decision is genuinely
  required, not merely when something is uncertain.
- `files` is an array of repository-relative paths; it may be empty but must be present.
- `suggestion` is the concrete remediation, not a restatement of the problem.
- **The finding bar is high here.** Every finding becomes a new task that a coder
  implements and a reviewer re-reviews, costing a full loop. Raise only findings worth
  that. Do not raise style preferences, do not re-litigate a design the branch has
  already committed to, and do not raise anything already noted in `completed.md` as
  deliberate.
- Use only the keys above. No `severity`, no `reviewer`, no `axis`, no `kind`.

## Prompt 1 — `standards.md.j2` (standards axis)

**Source:** `/Users/andrew/Documents/code/Sandcastle-loop/two-axis-agent-system-prompt-prd.md`
— port **only the standards axis**. Also read
`/Users/andrew/Documents/code/Sandcastle-loop/extra-two-axis-review-prompt-prd.md`
for the method sections.

The standards axis looks for: concrete violations of documented project standards,
architectural decisions, local conventions, and operational constraints.

Keep verbatim: the operating rules, the review method, the finding bar, and the source
prompt's key restraint — **do not invent standards.** If the repository has no
`AGENTS.md` / `CLAUDE.md` / docs stating a rule, infer a standard only from a clear,
repeated local convention in nearby code, and say which code you inferred it from.

The two-axis source keeps two axes in one output with an `axis` field. Here the axes
are two separate agents with two separate files, so **delete the `axis` field and every
instruction about keeping the axes separate** — this prompt only does the standards
axis and must say so up front ("Judge only documented-standards conformance. A missing
or incorrect behavior relative to the issue is out of scope for you; another reviewer
covers it.").

## Prompt 2 — `spec.md.j2` (spec axis)

**Source:** the same `two-axis-agent-system-prompt-prd.md` — port **only the spec axis**.

The spec axis looks for: missing, incorrect, or contradictory behavior relative to the
parent issue's requirements, intent, and acceptance criteria.

Same treatment: drop the `axis` field, state up front that documented-standards
violations are another reviewer's job, and keep the operating rules / review method /
finding bar.

This is the one reviewer running on the frontier model (`kimi-k3`), because judging
"did this branch actually do what the issue asked" across a multi-task branch is the
hardest call in the loop. Give it the parent issue's acceptance criteria and
`completed.md` as the two things to reconcile.

## Prompt 3 — `quality.md.j2`

**Source:** `/Users/andrew/Documents/code/Sandcastle-loop/code-quality-agent-system-prompt-prd.md`
(and `code-quality-user-prompt-prd.md`, `extra-code-review-prompt-prd.md`).

Port the operating rules, review target, finding bar, and severity sections. Map the
source's severity vocabulary onto the finding bar in prose — there is no `severity`
field in this contract, so the bar becomes "only raise it if it clears the bar", not
"raise it and label it low".

This reviewer covers correctness, maintainability, and the aspect checks (tests,
errors, types/contracts, concurrency/lifecycle, persistence/IO, security/auth,
config/build) across the whole branch, as opposed to per-task conformance.

## Prompt 4 — `extra_decompose.md.j2`

**Source:** `/Users/andrew/Documents/code/Sandcastle-loop/extra-issue-decomposer-prompt-prd.md`.

**Runs as:** node `extra_decompose`, class `.review-frontier`, model `kimi-k3`.

**Inputs:**

- `/tmp/fabro/extra/standards.json`
- `/tmp/fabro/extra/spec.json`
- `/tmp/fabro/extra/quality.json`
- `/tmp/fabro/extra/changed_files.txt`, `/tmp/fabro/extra/diffstat.txt`
- `/tmp/fabro/issue.json`, `/tmp/fabro/completed.md`
- the repository checkout, read-only

Any of the three findings files may have `"decision": "approve"` with an empty
`findings` array; that is normal, and a run where all three approve produces
`"status": "no_work"`.

**Output — write to `/tmp/fabro/extra/followups.json`:**

```json
{
  "status": "issues" | "no_work" | "needs_human_review",
  "summary": "one or two sentences",
  "issues": [
    {
      "id": "stable-kebab-case-key",
      "title": "short follow-up task title",
      "body": "self-contained body with ## User Story, ## Context, ## Acceptance Criteria",
      "files": ["path/to/file.ts"],
      "priority": "high" | "medium" | "low"
    }
  ]
}
```

This is the **same contract as `decomposition.json`** in task 03. Repeat its rules:
`issues` non-empty exactly when `status` is `issues`; every entry needs non-empty
string `id`, `title`, `body` and an array `files`; only those keys.

Two rules specific to this stage — state both prominently:

1. **`id` stability is load-bearing.** `extra_gate` merges these into
   `/tmp/fabro/tasks.json` and **drops any entry whose `id` already exists there**.
   Derive `id` deterministically from the finding's subject and scope so that the same
   defect raised in round 1 and again in round 2 produces the same `id` and is dropped
   the second time instead of looping forever. Do not put round numbers, dates,
   counters, or random suffixes in `id`.
2. **Merge across the three reviewers.** One defect often appears in two or three
   findings files. Emit **one** task for it, with the union of the cited `files`, not
   one task per reviewer.

Decomposition rules to keep from the source: one actionable implementation slice per
task; merge overlapping findings; split genuinely independent work; if the findings are
all nits or already-addressed, emit `no_work`; if the findings contradict each other or
the branch is in a state no follow-up can safely fix, emit `needs_human_review`.

Drop from the source: `{{PRD_NUMBER}}` and all `{{*_PATH}}` placeholders (replaced by
the literal paths above), and the review-metadata input (no equivalent here).

## Done when

- All four files exist under `prompts/`.
- `grep -c '{{' prompts/standards.md.j2 prompts/spec.md.j2 prompts/quality.md.j2 prompts/extra_decompose.md.j2`
  is 0 for each (or only `{{ goal }}`).
- `grep -ci 'structured-result\|axis.*field\|PRD_BODY_PATH' ...` is 0 for each.
- Each of the four names its exact output path and shows its exact JSON shape.
- `standards.md.j2` says spec defects are out of scope; `spec.md.j2` says standards
  violations are out of scope.
- `extra_decompose.md.j2` states the `id`-stability rule and the cross-reviewer merge rule.

## Pitfalls

- The three reviewer contracts use `"approve"` (no `d`), while the per-task reviewer in
  task 05 uses `"approved"`. That asymmetry is deliberate and matches the gates in
  task 02 — do not "fix" it in either direction.
- `extra_decompose` uses `status`, not `decision`. The three reviewers use `decision`,
  not `status`. Getting these backwards fails the gate.
- Do not let the reviewers propose findings that ask for the branch to be reverted or
  restructured wholesale. Follow-ups are appended to the task list and implemented by
  the same loop; a "rewrite everything" follow-up will burn all six rework rounds and
  end at `human_rescue`.
- Only two extra-review rounds run. A reviewer that saves its real findings for
  "next round" never gets one.
