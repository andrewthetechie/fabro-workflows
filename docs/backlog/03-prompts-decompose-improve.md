# Task 03 — Prompts: `decompose.md.j2` and `improve.md.j2`

**Depends on:** nothing (contracts are fixed in `00-overview-and-contracts.md`).
**Do before:** task 08 (validation).
**LLM level:** local is fine — this is careful copy-editing against a fixed contract.

## Goal

Port two Sandcastle agent prompts into fabro prompt files that use the
**file-backed contract** instead of Sandcastle's Structured-result MCP tool.

Write exactly two files:

- `~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog/prompts/decompose.md.j2`
- `~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog/prompts/improve.md.j2`

Both **overwrite** whatever is there now (`decompose.md.j2` already exists and is
the old, wrong port — replace it wholesale).

## Rules that apply to every prompt file in tasks 03–06

Read these once; they are not repeated in tasks 04–06.

1. **No template variables except `{{ goal }}`.** Fabro promotes undefined template
   variables to hard errors at run admission, so a stray `{{ISSUE_NUMBER}}` from a
   Sandcastle source will kill the run before it starts. Delete every
   `{{PLACEHOLDER}}`; the data it carried now comes from a file the prompt tells the
   agent to read. You do not have to use `{{ goal }}` at all.
2. **No Structured-result MCP.** Sandcastle's "submit through
   `structured-result_submit_*`" mechanism does not exist here. Replace every mention
   with: write the JSON to the named path under `/tmp/fabro/`.
3. **No GitHub.** Strip every `gh`/GitHub API/issue-mutation instruction. Deterministic
   command stages own all GitHub work.
4. **No git writes.** Strip `git add` / `git commit` / `git push` / branch switching.
   Fabro's checkpoint layer commits the working tree after every stage.
5. **Keep the instruction bodies as close to verbatim as you can.** You are changing
   the I/O contract and removing dead mechanisms, not rewriting the agent's judgment
   rules. When in doubt, keep the Sandcastle sentence.
6. **The JSON must be exactly the contract below** — no extra keys, no `kind` field,
   no markdown fence around the file contents. The file holds raw JSON.
7. End every prompt with an explicit final instruction: write the file, then stop.
   Do not print the JSON in chat.

## Prompt 1 — `decompose.md.j2`

**Sandcastle sources** (read both, port the union):

- `/Users/andrew/Documents/code/Sandcastle-loop/initial-issue-decomposer-agent-system-prompt-prd.md`
- `/Users/andrew/Documents/code/Sandcastle-loop/initial-issue-decomposer-user-prompt-prd.md`

**Runs as:** node `decompose`, class `.decomp`, model `long-context`.

**Inputs the prompt must tell the agent to read:**

- `/tmp/fabro/issue.json` — the full parent issue, as produced by
  `gh issue view N --json number,title,body,labels,comments`. The body is at `.body`,
  the title at `.title`, the number at `.number`, and human discussion at `.comments`
  (an array of objects with `.body`). The issue body is primary; comments are
  supplemental.
- The repository checkout in the current working directory, **read-only**.

**Output contract — write to `/tmp/fabro/decomposition.json`:**

```json
{
  "status": "issues" | "no_work" | "needs_human_review",
  "summary": "one or two sentences describing the decomposition result",
  "issues": [
    {
      "id": "stable-kebab-case-key-derived-from-title-and-scope",
      "title": "short child task title",
      "body": "self-contained body with ## User Story, ## Context, ## Acceptance Criteria",
      "files": ["path/to/file.ts"],
      "priority": "high" | "medium" | "low"
    }
  ]
}
```

Rules the prompt must state explicitly, because the gate enforces them and a
violation costs a whole retry round:

- `status` is `issues` **only** when `issues` has at least one entry.
- `status` is `no_work` when there is no actionable implementation work; `issues` is `[]`.
- `status` is `needs_human_review` when safe decomposition is impossible; `issues` is `[]`,
  and `summary` explains the blocker.
- Every entry needs a **non-empty string** `id`, a **non-empty string** `title`, a
  **non-empty string** `body`, and `files` must be an **array** (it may be empty).
  The gate rejects the whole file if any entry fails this.
- `id` must be stable and human-readable, derived from the title and scope
  (e.g. `add-gameweek-score-column`). Later stages dedupe follow-up work by `id`, so
  the same slice of work must produce the same `id`.
- `files` holds repository-relative paths only, unique within an entry.
- Use only the keys above. No `kind`, no `dedupe_key`, no `depends_on`,
  no `needs_human_review_reason`.

**Mapping notes from the Sandcastle source:**

| Sandcastle | Here |
|---|---|
| `structured-result_submit_initial_issue_decomposition` | write `/tmp/fabro/decomposition.json` |
| `dedupe_key` | `id` |
| `depends_on` | **dropped** — tasks run sequentially in array order, so order the array so prerequisites come first |
| `needs_human_review_reason` | folded into `summary` |
| `kind` | **dropped** |
| `{{PARENT_ISSUE_NUMBER}}` / `{{PARENT_ISSUE_TITLE}}` / `{{PARENT_CONTEXT}}` | "read `/tmp/fabro/issue.json`" |

**Keep verbatim** (these are the value of the source prompt): the read-only rules,
the decomposition rules, the issue-draft rules (inspect the nearest sibling
implementation, name the file to mirror, state interface rules in the body, require
the three body sections), and "ask no clarifying questions".

## Prompt 2 — `improve.md.j2`

**Sandcastle sources:**

- `/Users/andrew/Documents/code/Sandcastle-loop/subtask-improvement-agent-system-prompt-prd.md`
- `/Users/andrew/Documents/code/Sandcastle-loop/subtask-improvement-user-prompt-prd.md`

**Runs as:** node `improve`, class `.improve`, model `glm-5.3`. Runs **once per task**,
immediately before that task's first coder attempt.

**Inputs the prompt must tell the agent to read:**

- `/tmp/fabro/current_task.json` — the single task object being improved
  (`{id, title, body, files, priority, source}`).
- `/tmp/fabro/issue.json` — the parent issue, for reporter intent and human discussion.
- The repository checkout, **read-only**, at its **current** state.

**The "current accumulation SHA" rule matters here.** Sandcastle passes an explicit
accumulation SHA. Here, the workspace **is** the accumulation branch: earlier tasks in
this run have already been implemented and committed into the checkout the agent is
looking at. So the prompt must say: judge the task against the repository exactly as
it is right now in the working directory, not against the parent issue's original
state. That is what makes `redundant` detectable — an earlier task in this same run
may already have done the work.

**Output contract — write to `/tmp/fabro/improve_result.json`:**

```json
{
  "disposition": "ready" | "redundant" | "needs_human",
  "reason": "one or two sentences justifying the disposition",
  "task": {
    "id": "unchanged id from current_task.json",
    "title": "final title",
    "body": "final self-contained body",
    "files": ["path/to/file.ts"],
    "priority": "high" | "medium" | "low"
  }
}
```

Rules the prompt must state explicitly:

- `ready` — the task is implementation-ready. `task` is **required** and must carry a
  non-empty string `title` and a non-empty string `body`. Keep `id` byte-for-byte
  from `current_task.json`. If the task needed no changes, return it unchanged; if it
  needed sharpening, return the improved version. The gate overwrites
  `current_task.json` with `.task`, so whatever you put there is what the coder gets.
- `redundant` — current repository evidence proves the work is already done or is a
  duplicate of work already landed in this run. `reason` must cite the concrete
  evidence (file, symbol, or commit). `task` may be omitted.
- `needs_human` — the task cannot be made implementable without a human decision.
  `reason` must explain the blocker. `task` may be omitted.
- Never invent an assumption. Label unsupported or ambiguous claims as open questions
  inside the proposed body rather than asserting them.

**Mapping notes:**

| Sandcastle | Here |
|---|---|
| `structured-result_submit_subtask_improvement` | write `/tmp/fabro/improve_result.json` |
| `outcome: "improved"` and `outcome: "unchanged"` | both become `disposition: "ready"` |
| `outcome: "redundant"` | `disposition: "redundant"` |
| (no equivalent) | `disposition: "needs_human"` — new, for the blocker case |
| `proposed_title` / `proposed_body` | `task.title` / `task.body` |
| `changes` / `evidence` / `close_reason` | folded into `reason` |
| `{{ACCUMULATION_HEAD_SHA}}` / `{{ORIGINAL_FORK_SHA}}` | "the checkout as it is right now" |
| `{{SUBTASK_BODY}}` / `{{SUBTASK_DISCUSSION}}` / `{{ACTIVE_SIBLINGS}}` | `/tmp/fabro/current_task.json` + `/tmp/fabro/issue.json` |

**Keep verbatim:** the boundaries section (inspect only; no edits, no commits, no
network, no GitHub, no issue mutation), the "preserve reporter intent and human
discussion" rule, the honesty-about-unknowns rule, and "ask no questions".

Drop the evidence-ledger classification vocabulary (`Verified` / `Contradicted` /
`Unsupported` / `Ambiguous` / `Outdated-Risky`) as a required output structure — there
is no field for it — but keep the underlying instruction that every material claim
must have a concrete source, expressed in prose.

## Done when

- Both files exist at the two paths above.
- `grep -c '{{' prompts/decompose.md.j2 prompts/improve.md.j2` returns 0 for both,
  or only matches `{{ goal }}`.
- `grep -ci 'structured-result\|gh issue\|gh pr\|git commit\|git push\|dedupe_key' prompts/decompose.md.j2 prompts/improve.md.j2`
  returns 0 for both.
- Each file names its output path (`/tmp/fabro/decomposition.json`,
  `/tmp/fabro/improve_result.json`) and shows the exact JSON shape above.
- The old `prompts/decompose.md.j2` content is gone (it referenced `@schemas/` and an
  array-root schema; if you can still grep `schemas` in it, you did not overwrite it).

## Pitfalls

- Do **not** re-add an `output_schema` attribute or a `@schemas/...` reference
  anywhere. The whole redesign exists because that mechanism silently disables
  routing. See `00-overview-and-contracts.md` §"Why this exists".
- Do **not** tell the agent to print the JSON in chat "as well as" writing the file.
  It wastes a lot of tokens on large decompositions and nothing reads it.
- The gate's retry budget is **2 attempts**. The prompt has to be precise enough that
  attempt 1 is usually valid; a vague contract description costs a full agent round.
- A JSON file with a leading ```` ```json ```` fence is invalid to `jq` and will fail
  the gate. Say so in the prompt: the file contains raw JSON and nothing else.
