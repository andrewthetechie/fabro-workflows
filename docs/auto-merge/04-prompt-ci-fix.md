# Task 04 — The `ci_fix` prompt and its contract

**Depends on:** nothing. **Blocks:** 03. **LLM level:** yes.

New file: `.fabro/workflows/pr-review/prompts/ci_fix.md.j2`.

## Why not reuse `review_fix`

`review_fix` is the obvious candidate and the wrong one, for two reasons.

It writes `/tmp/fabro/review/fix_result.json`. Re-running it overwrites the `risk`
and `summary` that `merge_gate` is about to read and that `deliver` has already acted
on — the run would grade itself again, after the decision, on a different diff.

And its job is different. `review_fix` weighs reviewer findings and decides which to
apply. `ci_fix` has one failing check and one question: what makes it pass. Handing
that job a prompt full of finding-disposition machinery invites it to re-review.

So: separate prompt, separate output file, separate gate.

## What the agent is given

- `/tmp/fabro/feedback/ci_fix.md` — the failing check names and the `gh run view
  --log-failed` output `watch_checks` captured.
- `/tmp/fabro/review/changed_files.txt` — the files this PR already touches. **This is
  a ceiling, not a suggestion.**
- The usual diff and PR context the other `pr-review` prompts get.

## What the prompt must say

**The check is the spec.** The job is to make the named check pass. Not to improve the
code, not to fix other checks that are already passing, not to act on anything the
agent notices along the way.

**The file list is a hard boundary.** Editing a file not in `changed_files.txt` fails
the gate and ends the merge. If the fix genuinely requires a new file, the answer is
`cannot_fix` with the reason — that is a legitimate, non-failing outcome, and it means
a human reads the PR.

Say why, because a model told "you may not create files" will otherwise work around
it: the reviewers saw a specific diff and rated its risk. A file outside that diff was
never reviewed, and this PR is about to merge with no further human reading. The
boundary is what makes "no re-review after a CI fix" safe.

**Do not touch CI configuration to make CI pass.** Editing `.github/workflows/`,
disabling a test, adding a skip marker, or loosening a lint rule is how a check goes
green without the problem going away. If the check is wrong, that is `cannot_fix`.
This deserves its own paragraph — it is the most likely way a model satisfies this
prompt dishonestly.

**Do not run `git`.** The standing rule, with the two recorded exceptions
(`pr-review`'s rebase agent and `backlog`'s `resolve_merge`), neither of which is this.
`ci_fix_gate` pushes.

## Output — `/tmp/fabro/review/ci_fix_result.json`

Raw JSON, no fence, no commentary, these keys only:

```json
{
  "outcome": "fixed" | "cannot_fix",
  "scope": "ci_only",
  "checks": ["lint"],
  "files": ["frontend/src/App.tsx"],
  "summary": "what was failing, what changed, and why that makes the check pass",
  "reason": "required when outcome is cannot_fix: what a human must decide or do"
}
```

Rules the gate enforces:

- `outcome` is exactly one of the two values. There is no `needs_human` — `cannot_fix`
  *is* the ask-a-human outcome, and it is terminal for the merge, not for the run.
- `scope` is the literal string `ci_only`. It is a declaration the agent is making,
  and `ci_fix_gate` verifies it against git independently. A declaration that does not
  match the working tree is a failed gate.
- `fixed` requires a non-empty `files`, and every entry must already appear in
  `changed_files.txt`.
- `cannot_fix` requires `reason`, and it is published verbatim in the PR comment.
  Write it for the maintainer.
- `summary` is published verbatim too. Say what was failing and why the change fixes
  it — "fixed the lint error" tells a reader nothing they did not know.

## Retry shape

`ci_fix_gate` does **not** give the agent a repair turn the way `fix_gate` does. A
malformed `ci_fix_result.json` is `ci_fix_ok=false` and routes to `report_blocked`.

The reason is that the ladder already is the retry: `ci_fix_t1` failing routes to
`watch_checks`, which sees CI still red and dispatches `ci_fix_t2` on a stronger model.
Adding a per-node repair turn on top would give four agent invocations for "two
attempts to fix failing CI", and the budget from `merge_deadline` would absorb the
difference silently.

## Acceptance

- The prompt names the file boundary, the CI-configuration prohibition and the
  no-`git` rule explicitly, each with its reason.
- `jinja2` renders it with the same context the other `pr-review` prompts get.
- `fabro validate` resolves `@prompts/ci_fix.md.j2` from both `ci_fix_t1` and
  `ci_fix_t2`.
- A dry run against a real red check produces a `ci_fix_result.json` that
  `ci_fix_gate`'s validation accepts.
