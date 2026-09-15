# Task 02 — Rebase / conflict-resolution prompt

**Depends on:** 00. **Blocks:** 06. **LLM level:** local is fine.

## Goal

Write `~/.fabro-deploy/fabro-workflows/.fabro/workflows/pr-review/prompts/rebase.md.j2`.

One file, used by **both** `rebase_agent_t1` (`coders`) and `rebase_agent_t2`
(`glm-5.3`). It must not mention a tier or a model — it cannot tell which it is.

**Source:** `/Users/andrew/Documents/code/Sandcastle-loop/rebase-agent-system-prompt-prd.md`
and `rebase-user-prompt-prd.md`.

## The one agent allowed to run git

Every other agent in this deployment is forbidden from touching git. This one **must**
use it: `git rebase`, `git add`, `git rebase --continue`, `git rebase --abort`.

State that exception explicitly and bound it: the agent may run git **only** to complete
this rebase. It must never `git push`, never `git commit` outside `--continue`, never
call GitHub, never change labels or comment.

## Why the agent does the whole rebase

`rebase_check` has already tried a plain `git rebase` and **aborted** on conflict, so the
working tree is clean when the agent starts. The agent runs the rebase itself.

Say why, because a model that finds a clean tree will otherwise assume there is nothing
to do: fabro checkpoints after every stage with `git add -A && git commit`, which would
commit conflict markers if a conflicted rebase were left across a stage boundary. The
conflict therefore cannot be handed over in the tree — it has to be recreated here.

## Inputs

- `/tmp/fabro/pre_rebase_sha` — the PR head before any rebase attempt. Name this
  file literally; do not substitute a glob to satisfy a grep.
- `/tmp/fabro/target_sha` — the base branch tip to rebase onto
- `/tmp/fabro/base_ref` — the base branch name
- `/tmp/fabro/pr.json` — PR metadata, for intent when resolving

## Required procedure

1. `git rebase origin/<base>`.
2. For each conflict: open the file, understand **both** intents — what the PR is doing
   and what landed on the base — and resolve so both survive. Keep the PR's change;
   take the base's updates where they do not overlap.
3. `git add <file>` then `GIT_EDITOR=true git rebase --continue`, repeating until the
   rebase completes. The env var goes on the same command line every time, not in an
   `export`: each agent command may run in a fresh shell, and a `--continue` that
   opens an editor hangs the stage until its timeout.
4. Run the narrowest validation that exercises what you touched. Do **not** run the full
   test suite; a later stage runs `./.fabro/ci.sh`.
5. Leave a clean tree: no conflict markers, no rebase in progress.

## The escape hatch

If a resolution cannot be justified from the code — genuinely ambiguous intent,
overlapping rewrites of the same logic — **stop**:

1. `git rebase --abort`
2. Write `outcome: "unresolved"` with the ambiguity explained in `diagnostics`.

Say plainly that `unresolved` is a correct, expected outcome and always better than a
guess. A wrong conflict resolution silently corrupts a PR that a human will then merge.

## Output — `/tmp/fabro/rebase_result.json`

```json
{
  "outcome": "resolved" | "unresolved",
  "summary": "one or two sentences",
  "conflicted_files": ["path/to/file.ts"],
  "resolution_summaries": ["why both intents are preserved in this file"],
  "diagnostics": ["sanitized detail, required when unresolved"]
}
```

Mapping from the Sandcastle contract: drop `kind`, `pre_rebase_sha`,
`target_mainline_sha` and `rebased_sha` (the gate reads git directly, so echoing SHAs
adds a thing to get wrong); keep `outcome`, `conflicted_files`, `resolution_summaries`,
`diagnostics`; add `summary`.

Drop the Structured-result MCP mechanism and the `{{PRE_REBASE_SHA}}` /
`{{TARGET_MAINLINE_SHA}}` placeholders — both come from files now.

The Sandcastle prompt tells the agent to use an installed `rebase-on-main` skill. There
is a `rebase-on-main-skill/` directory in the Sandcastle repo and fabro does discover
`.fabro/skills/` under the git root, so porting it is possible — **but do not do it in
this task.** Write the procedure inline. Porting a skill is a separate change with its
own failure modes.

## Verification is not the prompt's job

`rebase_gate` independently checks for an in-progress rebase, unmerged paths, conflict
markers and whether the base is now an ancestor of HEAD. It does not read
`rebase_result.json` to decide — the file is for humans reading the run.

Say so in the prompt. With `coders` on tier 1, "the agent said it was resolved" is not
evidence, and an agent told its claims will be checked behaves better.

## Done when

- The file exists; `grep -c '{{' prompts/rebase.md.j2` is 0.
- It names `/tmp/fabro/rebase_result.json` and shows the exact JSON shape.
- It states the git exception and its bounds, and that `unresolved` is a good outcome.
- It mentions no tier, model name, or attempt number.
- `grep -c 'structured-result\|{{PRE_REBASE_SHA}}\|rebase-on-main' prompts/rebase.md.j2` is 0.
  Case-**sensitive**, and the placeholder is matched in its braces: the point is that
  Sandcastle's `{{PRE_REBASE_SHA}}` template variable is gone, not that the file may
  never name `/tmp/fabro/pre_rebase_sha`. A `-i` grep here is unsatisfiable, because
  the canonical input file is spelled with those very letters.

## Pitfalls

- Do not let the prompt imply the agent may `git push`. Delivery is `deliver`'s job and
  a stray push here would bypass `--force-with-lease`.
- Do not tell it to `git commit`. `git rebase --continue` is the only commit it makes.
- Do not put spec commentary in the prompt file, in an HTML comment or otherwise. The
  agent reads the whole file. If a task instruction conflicts with itself, fix the
  task.
- If the agent aborts, it must leave HEAD at `pre_rebase_sha`, not at some half state.
