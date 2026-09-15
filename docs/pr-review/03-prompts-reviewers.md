# Task 03 — Standards and spec reviewer prompts

**Depends on:** 00. **Blocks:** 06. **LLM level:** local is fine.

## Goal

Write two files under `.fabro/workflows/pr-review/prompts/`:

- `standards.md.j2` — from `Sandcastle-loop/pr-standards-review-agent-system-prompt.md`
  (+ `pr-standards-review-user-prompt.md`)
- `spec.md.j2` — from `pr-spec-review-agent-system-prompt.md`
  (+ `pr-spec-review-user-prompt.md`)

Both run on `glm-5.3`, read-only, and feed the `review_fix` agent in task 04.

## Porting rules (same as the backlog series)

1. **No template variables.** Delete every `{{PLACEHOLDER}}`; the data comes from files.
2. **No Structured-result MCP.** Write JSON to the named path instead.
3. **No GitHub calls, no git writes.** These reviewers are strictly read-only.
4. Keep the instruction bodies close to verbatim — the trust boundary, method, finding
   bar and Fowler smell baseline are the value.
5. The file holds raw JSON. No markdown fence.

## Shared inputs

| Path | What it is |
|---|---|
| `/tmp/fabro/review/diff.patch` | `git diff origin/<base>...HEAD` after rebase |
| `/tmp/fabro/review/diffstat.txt`, `changed_files.txt`, `commits.txt` | scope of the PR |
| `/tmp/fabro/pr.json` | number, title, body, refs, labels |
| `/tmp/fabro/linked_issues.json` | linked issues; **may be `[]`** |

The repository checkout is available read-only. Say that `./.fabro/ci.sh` has **not**
run yet at this point — unlike the backlog reviewers, these run before CI, so they must
not assume a green build.

Two Sandcastle inputs that do not exist here — say so, or the model will hunt for them:
there is no host-selected `{{REVIEW_ASPECTS}}` list and no `{{ECOSYSTEMS}}` detection.

## Shared output contract

`standards.md.j2` → `/tmp/fabro/review/standards.json`
`spec.md.j2` → `/tmp/fabro/review/spec.json`

```json
{
  "decision": "approve" | "findings" | "needs_human_review",
  "summary": "one or two sentences stating the verdict and why",
  "findings": [
    {
      "id": "STD-001",
      "severity": "info" | "warning" | "error",
      "message": "what is wrong and what must change",
      "files": ["path/to/file.ts"],
      "line": 42,
      "suggestion": "the concrete remediation"
    }
  ]
}
```

- `approve` — nothing worth changing; `findings` is `[]`.
- `findings` — at least one; `findings` must be non-empty.
- `needs_human_review` — **this ends the run** at `mark_needs_human`. Only for inputs
  that cannot be judged safely, not for ordinary uncertainty.
- `files` is an array, possibly empty, always present.
- `id` is consecutive, `STD-###` for standards and `SPEC-###` for spec. The fixer cites
  these ids in `fixes_applied` / `not_fixed`, and the PR comment prints them, so they
  are the join key across the whole report.
- `severity` is `error` | `warning` | `info`. `line` may be `null`.
- Only those keys. No `kind`, no `axis`, no `risk`, no `confidence`.

## Axis separation

Each prompt must say what is **not** its job, in one sentence near the top:

- `standards.md.j2`: judge conformance to documented standards, architectural decisions
  and strong local conventions. Whether the PR does what was asked is another
  reviewer's job.
- `spec.md.j2`: judge whether the PR does what the linked issue and PR body asked for.
  Style and standards violations are another reviewer's job.

Keep the standards prompt's **"do not invent standards"** rule: if `AGENTS.md` /
`CLAUDE.md` / docs state no rule, infer one only from a clear repeated local convention,
and name the code it was inferred from.

Keep the spec prompt's **source precedence** section, adapted: linked issue first, then
PR body, then commit messages. With `linked_issues.json` empty, judge against the PR
body and say in the summary that no linked issue was available.

## The findings are published

Say so in both prompts. Every finding is printed verbatim in a comment on the pull
request, so `message` is written for a maintainer who has not seen the diff: problem,
concrete impact, required outcome, citing the governing rule and the code it was read
from. Without this the reviewers write one-liners — which is what v1 produced.

## Finding bar

Both prompts already have one; keep it and add the local consequence: **every finding
is handed to a fixer that will edit the code.** A weak finding does not produce a
comment, it produces a diff. Do not raise style preferences, speculation, or anything
the PR deliberately decided.

## Done when

- Both files exist; `grep -c '{{'` is 0 for each.
- `grep -ci 'structured-result\|REVIEW_ASPECTS\|ECOSYSTEMS\|{{DIFF'` is 0 for each.
- Each names its own output path and shows the exact JSON shape.
- Each states the other axis is out of scope.
- Neither instructs any git or `gh` command.

## Pitfalls

- The `decision` values are `approve` / `findings` / `needs_human_review` — note
  `approve`, not `approved`. The gates in task 01 test these exact strings.
- `needs_human_review` is a terminal route here, not a retry. Do not describe it as
  "ask for help and continue".
- Do not have either reviewer propose edits directly; they produce findings, the fixer
  edits.
