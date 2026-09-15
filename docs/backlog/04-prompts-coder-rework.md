# Task 04 — Prompts: `coder.md.j2` and `rework.md.j2`

**Depends on:** nothing. **Do before:** task 08.
**LLM level:** local is fine.
**Read first:** task 03 §"Rules that apply to every prompt file in tasks 03–06".

## Goal

Write the two coding prompts. These are the only agent stages with **no gate after
them** — the coder's deliverable is the working tree, not a JSON file.

Write exactly two files:

- `~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog/prompts/coder.md.j2`
- `~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog/prompts/rework.md.j2`

`coder.md.j2` already exists and is the **best of the previous ports** — keep its
scope-discipline body, its anti-patterns section, and its `<promise>COMPLETE</promise>`
completion protocol. What changes is the I/O contract (files in, working tree out) and
the removal of the commit instructions. Read the existing file before you overwrite it
and plunder it.

`rework.md.j2` is new. It is `coder.md.j2`'s body plus a feedback section.

## The one rule that differs from Sandcastle: do not commit

Sandcastle's coder and rework prompts both say `git add` + `git commit` is
**mandatory** because "the host only sees committed history". **That is false here and
must be removed from both prompts.**

In fabro, the checkpoint layer commits the whole working tree to the run branch after
every stage completes. The `prep_review` stage that runs next does
`git diff $(cat /tmp/fabro/task_base_sha)..HEAD`, and by then fabro has already
committed the agent's edits. An agent that commits itself is not harmful, but an agent
that *waits* to verify `git status -s` is empty will stall, and one that runs
`git push` can break the run.

So the completion protocol becomes:

- Leave the changes in the working tree. Do not run `git add`, `git commit`,
  `git push`, `git checkout -b`, `git merge`, or `git rebase`.
- The completion check is no longer "`git log -1 --stat` shows my changes and
  `git status -s` is empty". Replace it with: "`git status -s` shows exactly the files
  you intended to change, and nothing else."
- Then emit `<promise>COMPLETE</promise>`.

Keep the three terminal markers exactly as Sandcastle has them, because they are how a
human reading the transcript distinguishes the three exits:

- `<promise>COMPLETE</promise>` — work done (or deliberately nothing done).
- `<blocked>one or two sentences</blocked>` then `<promise>COMPLETE</promise>` — the
  change cannot be made in scope, or requirements are genuinely ambiguous. Change
  nothing.
- `<already_satisfied>one or two sentences citing existing files or behavior</already_satisfied>`
  then `<promise>COMPLETE</promise>` — acceptance criteria already hold. Change nothing.

## Prompt 1 — `coder.md.j2`

**Sandcastle sources** (port the union; the existing fabro port already covers most of it):

- `/Users/andrew/Documents/code/Sandcastle-loop/coder-agent-system-prompt-prd.md`
- `/Users/andrew/Documents/code/Sandcastle-loop/coder-user-prompt-prd.md`
- `/Users/andrew/Documents/code/Sandcastle-loop/implement-prompt-prd.md` — the longest
  and most useful of the three; its `# Scope`, `# Anti-patterns — do NOT do these`,
  `# Process`, and `# Example: scope discipline` sections are the body you want.

**Runs as:** node `coder`, class `.coder`, model `coders`. Also the body of
`rework_t1..t4`.

**Inputs the prompt must tell the agent to read, in this order:**

- `/tmp/fabro/current_task.json` — **the task to implement.** `.title` and `.body` are
  the spec; `.body` carries the user story, context, and acceptance criteria;
  `.files` is the decomposer's guess at which files are involved (a hint, not a
  contract).
- `/tmp/fabro/issue.json` — the parent issue, for reporter intent and human discussion
  in `.comments`. Supplemental: `current_task.json` wins where they disagree.

**Deliverable:** edits in the working tree. Nothing else.

**Must state explicitly:**

- **Do not run the test suite.** A separate `validate` stage runs
  `./.fabro/setup.sh && ./.fabro/ci.sh` after you finish. Run only the narrowest
  useful check for the surface you changed (a single test file, a typecheck of one
  package, a lint of the changed files) — and only when a cheap obvious command exists.
- **Do not call GitHub.** No `gh`, no API. Deterministic stages own all issue and PR work.
- **Do not write outside the repository checkout**, except that `/tmp/fabro/` is
  read-only input you may read.
- Follow the repository's `AGENTS.md` / `CLAUDE.md` for types, helpers, structure, and naming.
- Do not write narration before the first tool call.

**Keep verbatim from Sandcastle:** the scope definition (a file is in scope only if the
task body names it, the feature cannot work without it, or it is a new file an
acceptance criterion requires); the do-not-touch list; the "match local structure
rather than inlining a divergent copy" rule; the "~5 files, stop and re-read" heuristic;
"when unsure, leave the file alone"; and the whole missing-dependency procedure
(install the exact package from the error, one at a time, dev vs runtime by importer,
no version pinning, never delete or rewrite an import to silence an unresolved module).

## Prompt 2 — `rework.md.j2`

**Sandcastle sources:**

- `/Users/andrew/Documents/code/Sandcastle-loop/rework-agent-system-prompt-prd.md`
- `/Users/andrew/Documents/code/Sandcastle-loop/rework-user-prompt-prd.md`
- `/Users/andrew/Documents/code/Sandcastle-loop/rework-prompt-prd.md`

**Runs as:** nodes `rework_t1`, `rework_t2`, `rework_t3`, `rework_t4` — the same prompt
file at four escalating model tiers. The prompt must not mention a tier or a model
name; it cannot tell which one it is and must not try.

**Inputs, in priority order — state this order explicitly in the prompt:**

1. **Human guidance, if present.** If the run context contains human guidance from a
   rescue gate (`human.gate.text`), it **overrides everything else**. A human has
   looked at this run and told you what to do; do that, and treat the findings file as
   secondary context. This is the `[R] Retry with guidance` path out of `human_rescue`,
   and it lands on `rework_t4`.
2. `/tmp/fabro/feedback/rework.md` — **the findings. This is your entire scope.**
   Markdown, written by whichever stage rejected the previous attempt. It has three
   possible shapes, and the prompt should name all three so the agent is not surprised:
   - `## Review findings to fix` — a reviewer's summary plus a bulleted finding list.
   - `## Validation failed (exit N): ./.fabro/setup.sh && ./.fabro/ci.sh` — the tail of
     the build/test log. Fix the failure; do not disable, skip, or delete the failing test.
   - `## Diff too large` — the change exceeded 2 MB. Shrink or split it.

   A mainline merge conflict is deliberately **not** one of them any more: `next_task`
   routes it to the dedicated `resolve_merge` stage (task 08), not to a rework tier.
3. `/tmp/fabro/current_task.json` — the task being reworked. **If this file is missing
   or unreadable, do not stop.** Work directly from `/tmp/fabro/issue.json` instead.
   (A human rescue can route here before any task has been selected.)
4. `/tmp/fabro/issue.json` — parent issue, supplemental.

**Body:** the same scope discipline as `coder.md.j2`, tightened the way Sandcastle
tightens it for rework:

- In-scope files = the set the findings cite. For each finding: open the cited file,
  read ~10 lines of context, apply the smallest change that satisfies it. If a
  finding's remediation is unclear, implement only what its problem description
  directly requires.
- Do not inspect or edit files outside that set; do not refactor, rename, or
  restructure beyond what a finding requires; do not fix related issues you notice;
  do not rewrite the feature instead of applying the requested fix; do not add tests
  unless a finding asks for them.
- **"Expanding scope to satisfy a finding is worse than leaving it unfixed."** Keep
  that sentence.
- If one finding cannot be addressed in scope, leave it, apply the in-scope fixes, and
  emit `<promise>COMPLETE</promise>`. The reviewer sees it again next round.
- If every finding requires out-of-scope edits, or the findings contradict each other,
  use the `<blocked>` exit.

Carry over the same no-commit, no-GitHub, no-full-test-suite, missing-dependency, and
completion-marker rules as `coder.md.j2`.

Drop Sandcastle's `## Host-only database validation` section — it is project-specific
to Sandcastle's Postgres setup and does not apply.

## Done when

- Both files exist at the two paths above.
- `grep -n 'git commit\|git add\|git push' prompts/coder.md.j2 prompts/rework.md.j2`
  returns nothing, **or** only lines that forbid those commands.
- `grep -c '{{' prompts/coder.md.j2 prompts/rework.md.j2` is 0 (or only `{{ goal }}`).
- Both files contain the literal string `<promise>COMPLETE</promise>`.
- `rework.md.j2` names all four feedback shapes and the `human.gate.text` precedence rule.
- Both files tell the agent not to run the full test suite.

## Pitfalls

- The biggest regression risk is re-introducing a planning step. The previous
  deployment had a separate `plan` stage whose output the coder could not see, so the
  coder re-planned from scratch and burned ~50 minutes per run. There is **no plan
  stage** now: `improve` produces the spec and the coder implements it directly. Do
  not add "first, write a plan" instructions to `coder.md.j2`.
- Do not delete `prompts/plan.md.j2` here — task 07 owns dead-file cleanup.
- `rework.md.j2` is used by four nodes. Anything you write that assumes "this is the
  first retry" or "this is the last chance" will be wrong three times out of four.
