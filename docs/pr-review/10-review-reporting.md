# Task 10 — Report the review on the pull request

**Depends on:** 07 (the first live run). **LLM level:** local is fine.

## Why this exists

Run `01M2JS6Y37TTQ2QDMQZK7GKB9N` reviewed lawncare-saas PR 2588 and every stage did
its job:

```
rebase_check   rebase_state: not_needed     standards_gate  standards_status: findings
spec_gate      spec_status: approve         fix_gate        fix_outcome: fixed
validate       ci_ok: true, fix_attempts: 0 deliver         delivered: true
```

The standards reviewer checked all 13 changed files against `CODING_STANDARDS.md`,
`docs/standards/python-backend.md`, the ADR constraints and the Fowler baseline, and
found one real violation: the `DeepStatusResponse` model docstring carried assembler
semantics, duplicated into the endpoint docstring and the generated OpenAPI
description. Spec approved with two named caveats. `review_fix` applied the finding
correctly in `b939019e`.

None of that reached the pull request. `deliver` posted:

```
Automated review complete.

- Rebased onto the tip of main
- Reviewed for correctness, documented standards and spec conformance
- Fixes applied; ./.fabro/setup.sh and ./.fabro/ci.sh green
```

A hardcoded string. The workflow produced the analysis and threw it away. The operator's
reading was that the run "didn't do anything valuable" — it did, invisibly.

## What changed

1. **Findings carry identity and grade.** `standards.json` and `spec.json` findings gain
   `id` (`STD-###` / `SPEC-###`), `severity` and `line`, and both prompts now say the
   findings are published verbatim, so they are written for a maintainer rather than as
   one-liners.
2. **The fixer reports dispositions.** `fix_result.json` replaces `changes[]` with
   `fixes_applied[]` and `not_fixed[]`, adds `risk` and `notes`, and `summary` becomes
   the comment's opening paragraph. Every reviewer finding must appear exactly once
   across the two arrays, cited by id. `fix_gate` rejects `fixed` with an empty
   `fixes_applied`.
3. **The comment is rendered.** `validate_input` writes `render.jq` and
   `render_comment.sh` to `/tmp/fabro`; `deliver` and `mark_needs_human` both call them.
   Layout is ported from Sandcastle's `renderPrReviewComment`
   (`pr-review-result.mts:192`): heading with risk, reviewed HEAD, the summary, a
   verdict table, Findings, Fixes applied, Not fixed and why, Commits, Notes.
4. **The commits that changed files are linked.** Checkpointing stays on, so the PR
   still gets one commit per stage and most are empty. The report walks
   `run_base_sha..HEAD`, keeps only commits with a non-empty `git diff-tree`, and links
   each as `<pr_url>/commits/<sha>` with its stage name and diffstat. That is the
   operator's decision: do not squash, make the real commits easy to find.

## Constraints learned here

- **Nothing copies a workflow's `scripts/` into the run sandbox.** Prompts are resolved
  host-side and inlined; backlog's `discord-notify.sh` runs in the server container from
  an absolute path. So the renderer has to be written by a command stage into `/tmp`.
- **Fabro's DOT parser turns `\n` into a real newline.** `printf '%s\n'` becomes a
  literal line break, and jq's `"\n"` becomes an unterminated string. Use
  `([10]|implode)` for a newline inside an embedded jq program, and keep `\"` as the
  only backslash in the file.
- `--slurpfile` fails on a missing or malformed file, so `render_comment.sh` validates
  each input with `jq empty` and substitutes `{}` before rendering.

## Done when

- A rendered report appears on the PR, naming each finding by id and each fix commit
  by sha.
- Task 06 check 10 passes on all three degraded paths.
