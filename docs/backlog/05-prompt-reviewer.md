# Task 05 — Prompt: `reviewer.md.j2` (per-task review)

**Depends on:** nothing. **Do before:** task 08.
**LLM level:** local is fine.
**Read first:** task 03 §"Rules that apply to every prompt file in tasks 03–06".

## Goal

Port Sandcastle's reviewer into the per-task review stage. This is the prompt whose
verdict drives the core loop: `approved` → `integrate` → next task;
`changes_requested` → `rework_router` → an escalating coder tier.

Write exactly one file:

- `~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog/prompts/reviewer.md.j2`

**Overwrite** the existing `prompts/reviewer.md.j2` — it is the old port and emits its
verdict through an `output_schema`, which is the exact mechanism that broke review
routing in production.

**Sandcastle sources** (read both; the second is the richer one):

- `/Users/andrew/Documents/code/Sandcastle-loop/reviewer-agent-system-prompt-prd.md`
- `/Users/andrew/Documents/code/Sandcastle-loop/review-prompt-prd.md`
- `/Users/andrew/Documents/code/Sandcastle-loop/reviewer-user-prompt-prd.md`

**Runs as:** node `review`, class `.review`, model `glm-5.3`. Deliberately **not** the
model that wrote the code — fresh eyes.

## Inputs

The prompt must tell the agent to read these, and must say the review is **read-only**:

| Path | What it is |
|---|---|
| `/tmp/fabro/review/diff.patch` | `git diff <task_base_sha>..HEAD` — the change under review, and the only thing being reviewed |
| `/tmp/fabro/review/diffstat.txt` | `git diff --stat` of the same range |
| `/tmp/fabro/review/changed_files.txt` | `git diff --name-only` of the same range |
| `/tmp/fabro/current_task.json` | the task this change was supposed to implement — `.title`, `.body` (acceptance criteria live here), `.files` |
| `/tmp/fabro/issue.json` | parent issue, for reporter intent; supplemental |

The repository checkout is available for **read-only** inspection when the diff alone
does not settle a question (e.g. to see the surrounding function, or to check whether a
helper the diff calls actually exists).

Two things Sandcastle passes that are **not** available here — say so, so the agent does
not hunt for them: there is no separate PRD document, and there is no host-selected
`REVIEW_ASPECTS` list. The aspect checks below are all always in play; apply the ones
the diff actually touches.

## Output contract — write to `/tmp/fabro/review/verdict.json`

```json
{
  "decision": "approved" | "changes_requested" | "needs_human_review",
  "summary": "one or two sentences stating the verdict and why",
  "findings": [
    {
      "severity": "blocking" | "nit",
      "message": "what is wrong, where, and what to change — specific enough to act on without re-deriving it"
    }
  ]
}
```

Rules the prompt must state explicitly:

- `approved` — no blocking findings. `findings` may still list `nit` entries or be `[]`.
  **Nits do not block.** An approved verdict with nits is a normal, good outcome.
- `changes_requested` — at least one `blocking` finding. `findings` must be non-empty.
  Every `blocking` entry must name the file and the concrete change required, because
  the next stage turns these bullets into the rework agent's entire scope
  (`review_gate` writes `.summary` and each `.findings[].message` into
  `/tmp/fabro/feedback/rework.md`). A finding phrased as a question, or as "consider
  looking at X", produces a wasted rework round.
- `needs_human_review` — the change cannot be judged without a human decision (the task
  spec contradicts the parent issue, the change touches something with no safe
  automated verdict, the diff is unreviewable). This routes the run to the human rescue
  gate, so use it only when that is genuinely warranted.
- `severity` is exactly `blocking` or `nit`. No other values.
- Use only the keys above. No `kind`, no `aspects`, no `confidence`.
- The file holds raw JSON — no markdown fence, nothing before or after it.

**Mapping notes:**

| Sandcastle | Here |
|---|---|
| structured-result submit tool | write `/tmp/fabro/review/verdict.json` |
| `{{DIFF}}` / `{{DIFF_STAT}}` / `{{CHANGED_FILES}}` | the three files under `/tmp/fabro/review/` |
| `{{ISSUE_BODY}}` / `{{ISSUE_COMMENTS}}` | `/tmp/fabro/current_task.json` + `/tmp/fabro/issue.json` |
| `{{PRD_BODY}}` | **dropped** — no PRD here |
| `{{REVIEW_ASPECTS}}` | **dropped** — all aspects always apply |
| `{{BASE_BRANCH}}` / `{{REVIEW_BASE_SHA}}` | the diff is already computed; do not recompute it |
| `!`git log ...`` shell interpolation | **dropped** — fabro prompts do not interpolate shell |
| finding `file` / `line` / `problem` / `remediation` fields | folded into one `message` string; keep the discipline of naming file and remediation inside it |

## What to keep verbatim

This is where the source prompt's value is — port these near-literally:

- **Operating rules** — review only the diff; do not edit anything; do not run the
  build or the test suite (a `validate` stage already ran `./.fabro/setup.sh &&
  ./.fabro/ci.sh` and it passed, otherwise you would not be here).
- **Finding bar** — the threshold a problem must clear to be `blocking`. This is the
  single most important section: it is what stops a reviewer from bouncing a correct
  change on taste.
- **Hard blocking patterns** — the enumerated list of things that are always blocking.
- **Documented standards** — defer to the repository's `AGENTS.md` / `CLAUDE.md` over
  personal preference.
- **Review method** and the per-aspect checks (`tests`, `errors`, `types-contracts`,
  `comments-docs`, `concurrency-lifecycle`, `persistence-io`, `security-auth`,
  `config-build`).
- **Approval discipline** — approve when the change satisfies the acceptance criteria
  and clears the finding bar, even if you would have written it differently.
- **Effort floor** and **finding quality** from `review-prompt-prd.md`.
- The three worked examples (approved / changes_requested / needs_human_review),
  rewritten to the JSON contract above.

Add one rule Sandcastle does not need, because our escalation ladder is capped at six
rounds: **scope the review to the task.** The diff implements one task out of a
sequence; changes that earlier tasks in this run already landed are not in this diff
and are not yours to judge. Judge this diff against `current_task.json`'s acceptance
criteria.

## Done when

- `prompts/reviewer.md.j2` exists and no longer mentions `output_schema` or `@schemas`.
- `grep -c '{{' prompts/reviewer.md.j2` is 0 (or only `{{ goal }}`).
- The file names `/tmp/fabro/review/verdict.json` as the output path and shows the
  exact JSON shape above.
- The file states that `blocking` findings must name the file and the required change.
- The file states that nits do not block approval.

## Pitfalls

- The old `prompts/reviewer.md.j2` and a stale root-level `verdict.schema.json` /
  `schemas/verdict.schema.json` describe a **different** verdict shape. Do not copy
  from them. Task 07 deletes the schema files.
- Do not add a `blocking_count` or similar derived field — `review_gate` reads only
  `.decision`, `.summary`, and `.findings[].message`.
- Do not instruct the reviewer to re-run tests. It doubles the stage cost and the
  `validate` stage already gated on a green `./.fabro/ci.sh`.
- The gate's retry budget is 2 attempts. Make the contract unmissable.
