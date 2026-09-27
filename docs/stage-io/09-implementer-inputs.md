# 09 · Implementers and the remaining readers: inputs only

Read `00-overview-and-contracts.md` first. Task 08 must be live. This task changes only how
these stages **read**. Their outputs stay hand-written until task 10, so their manifest
entries have no `output` for now, and the server gives them no `submit`.

## Stages

`coder`, `rework_t1`..`rework_t4` (one prompt, `rework.md.j2`), `improve`, `decompose`,
`extra_decompose`, `resolve_merge`, `review_merge.review_fix`, `review_merge.ci_fix_t1` and
`t2` (one prompt), `rebase_agent_t1` and `t2` in `pr-review` (one prompt), `scan` in
`arch-review`, and the triage phase's `improve` and `triage`.

Start with `coder` and `rework_*`: they have the most lead turns on a local box (task 01
baseline).

## Changes, for each prompt, one commit

1. Manifest: inputs in the prompt's order, `"live": true`, no `output`. Optional inputs get
   an `absent` sentence. `repomap.md` and `task-context.md` are optional ("It can be
   missing"), and the sentences say so.
2. Prompt: the "Read these" list becomes a call to `inputs`. Keep what the prompt says
   about **how to use** each input (for example "`current_task.json` is the task to
   implement; `.body` holds the acceptance criteria"). Only the reading moves into the tool.
3. Gate: the stage's output gate gets the reads-only form of C7. `served.json` holds the
   current visit and every required input is complete. That check goes inside the existing
   repair branch. A stage that has no gate of its own after it (check the edges) gets the
   check in the first command node after it. If that node cannot repair, it must route to
   the stage's existing failure path. Name that path in the commit message.
4. `rework.md.j2` names `/tmp/fabro/feedback/` for writing, and `resolve_merge` names
   `pre_merge_sha` for git. Paths that a prompt names for anything but reading stay in the
   prompt, and go in the manifest's `"also"` array (task 05).
5. `ops/test-task-gates.sh`: for each changed gate, a `served.json` fixture that passes, one
   with a stale visit, and one with a required input incomplete.

## `coder`: report a missing receipt, never route on it (Decision 8)

`coder` has `allow_partial=true` and a 180 m timeout, and a coder that times out has already
done work. It has no gate of its own: both `outcome=succeeded` and
`outcome=partially_succeeded` go to `autofix`, and everything else goes to `human_rescue`
(`backlog/workflow.fabro`, the `coder ->` edges). If a receipt check sends a partial coder
back, a long stage runs again. The decision: check `coder`'s receipt in the first
node after `autofix` that already judges the task (read the graph to find it), and only
report a missing receipt there. Do not route on it, because the review ladder already
judges the code. Write the chosen node in the commit message. Ask the operator if the
graph makes that impossible.

## Verification

On five runs for each stage: the first tool call is `mcp__io__inputs`; `lead_turns` median is
1 or less; `partial_reads` and `capped_reads` for these stages are 0. For `coder` on a local
box, compare `lead_secs` with the baseline. That comparison is the saving that this series
predicted (B3, "Evidence").
