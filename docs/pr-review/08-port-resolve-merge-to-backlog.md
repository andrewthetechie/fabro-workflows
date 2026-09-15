# Task 08 — Port the conflict-resolution node into the backlog workflow

**Depends on:** 07 (the design must have resolved a real conflict first).
**Blocks:** 09. **LLM level:** local is fine.

## The bug being fixed

`backlog`'s `next_task` merges `origin/main` between tasks — that is how the run branch
accumulates mainline while tasks land. On conflict it does this:

```sh
git merge --abort || true          # the conflict is destroyed here
echo '## Mainline merge conflict' > /tmp/fabro/feedback/rework.md
echo 'Resolve the conflict minimally...' >> ...
rebase_failed:true                  # routes to rework_router -> a coder tier
```

The merge is **aborted before any agent runs**, so there is no conflict in the tree.
And task 04's rework prompt forbids `git merge` and `git rebase`, so the agent cannot
recreate one either. It gets a clean tree, instructions to resolve a conflict, and no
permitted way to produce one — it no-ops or flails until `human_rescue`.

It has never fired because `origin/main` has not moved during a run. **Landing PRs from
this very workflow is what makes it reachable** — and once the schedules are re-enabled
across four repos, it will fire.

Aborting was not simply wrong, though: leaving the merge in progress means fabro's next
checkpoint (`git add -A && git commit`) stages the conflict-marked files, which git
treats as resolved, and **commits conflict markers onto the run branch**. Neither
abort nor leave-it is correct.

## The fix — the pr-review shape, ported

The same resolution as task 01's rebase ladder: the command stage aborts to restore a
clean tree, and a **dedicated agent stage performs the merge itself** and resolves it in
one stage, so the following checkpoint commits a resolved tree.

Changes to `.fabro/workflows/backlog/`:

**1. `next_task`** — keep the abort. Change only the feedback: it should say the merge
was attempted and aborted, and that a dedicated stage will redo it. Keep
`rebase_failed:true` (renaming the key is a bigger change than it looks — the edge
conditions and my earlier stale-context fix both reference it).

**2. New node `resolve_merge`** (agent, class `.coder-t4` → `glm-5.3`):

```
resolve_merge [label="Resolve the mainline merge", class="coder-t4", prompt="@prompts/resolve_merge.md.j2"]
```

Tier 4 deliberately: this is a correctness-critical merge onto an accumulating branch
that several later tasks build on. Unlike pr-review's rebase — which touches one PR —
a bad resolution here corrupts every remaining task in the run.

**3. New node `resolve_merge_gate`** (command, `output_schema="routing"`) — the same
four checks as pr-review's `rebase_gate`, adapted to merge:

- no merge in progress (`.git/MERGE_HEAD` absent)
- no unmerged paths (`git diff --name-only --diff-filter=U` empty)
- no conflict markers in tracked files
- `origin/main` is now an ancestor of HEAD

On failure: `git merge --abort`, `git reset --hard` to the pre-merge SHA, increment an
attempt counter, and route to `human_rescue` after one retry.

**4. New prompt `prompts/resolve_merge.md.j2`** — a **copy** of pr-review's
`rebase.md.j2`, adapted from rebase to merge. Per operator decision 14, this is
duplicated, not shared via `@../`: the path resolver has a containment check and
cross-workflow imports are unproven, and a broken `@` import is a run-*admission*
failure.

**5. Edges:**

```
next_task -> resolve_merge   [condition="context.rebase_failed=true"]
resolve_merge -> resolve_merge_gate   [condition="outcome=succeeded"]
resolve_merge -> resolve_merge_gate
resolve_merge_gate -> improve   [condition="context.merge_ok=true"]
resolve_merge_gate -> resolve_merge   [condition="context.merge_attempts=1"]
resolve_merge_gate -> human_rescue
```

The existing `next_task -> rework_router [condition="context.rebase_failed=true"]` edge
is **replaced** by the first of these. Sending a mainline conflict into the rework ladder
was the original mistake: rework is for review findings, and its prompt forbids git.

**6. Task 04's `rework.md.j2`** — remove `## Mainline merge conflict` from the four
feedback shapes it documents. That shape no longer reaches it.

## Verify

- Container `fabro validate` on the backlog workflow: still **35 nodes**, now **+2** →
  37 nodes, and the edge count grows by 5 and loses 1.
- `grep -c 'output_schema="routing"' workflow.fabro` goes 13 → 14.
- All command scripts still pass `sh -n`; jq programs still compile.
- The `#`-as-comment, class-selector and non-routing-`output_schema` greps stay clean.
- Re-run the stale-context invariant check: `rebase_failed` must still be written both
  `true` and `false`.

## Done when

- The backlog workflow has `resolve_merge` + `resolve_merge_gate` + the prompt.
- `next_task` no longer routes a mainline conflict into `rework_router`.
- `rework.md.j2` no longer claims to handle mainline conflicts.
- Container validation passes and the plan canonical in the backlog series
  (`.scratch/fabro-implementation-fixup/02-workflow-graph.md`) is updated to match, so
  re-transcribing still reproduces what is deployed.

## Pitfalls

- **Do not port this before task 07.** The whole point of the ordering is that backlog
  receives a design that has resolved a real conflict, rather than being the test bed
  for one.
- Backlog **merges**, it does not rebase (operator decision 13). The prompt must say
  merge, and the gate must check `MERGE_HEAD`, not `.git/rebase-merge`.
- Backlog is live and feeding real PRs. Validate before pushing, and remember that
  pushing to `fabro-workflows@main` changes production behaviour on the next fire.
