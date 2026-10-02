# 06. `improve` can never leave edits in the branch (C5)

## Outcome
If `improve` changes any tracked file, `improve_gate` reverts that change before anything
else happens, records it, and continues. The coder starts from a tree that `improve` did
not touch.

## Why
`improve` is read-only by its prompt, and that has not held. It edited 4 tracked files in
`01M3SN85YDECYRDVP3YYPYM6AM` (reverting them itself only because it re-read its prompt),
and in `01M3TN6GJK6PG1YEKCE786T16V` it stashed and popped the coder's earlier work. The
checkpoint after `improve` commits whatever the stage left, so a forgotten revert would
become the coder's starting point and show up in the PR diff. The git guard (05) blocks
the mechanisms but not `edit_file`. A deterministic check is the only guarantee.

## Change
`backlog/workflow.fabro`, node `improve_gate`: at the top of the script, before the
receipt check, add the C5 lines:
```sh
if ! git diff --quiet HEAD^ HEAD; then
  N=$(git diff --name-only HEAD^ HEAD | wc -l | tr -d ' ')
  git diff --name-only HEAD^ HEAD | sed 's/^/improve changed (reverted): /'
  git revert --no-edit --no-commit HEAD
  echo $N > /tmp/fabro/improve_touched_tree
fi
```
These lines must not write to stdout in a way that looks like JSON: the node routes on its
last JSON object, so plain `improve changed…` lines are fine. Add a `//` comment above the
node citing this task and the two runs. Do **not** change any disposition logic.

`next_task` deletes `/tmp/fabro/improve_touched_tree` with the other per-task files.

The shared triage phase's `improve` (arch-review, issue-triage) runs against a checkout
that nothing commits back to a PR branch, so it does not need the backstop. Say so in the
same comment.

## Acceptance
- `ops/test-task-gates.sh`, in a new real-git section:
  - a fixture repo where HEAD is an "improve checkpoint" that modified one file, added one
    and deleted one: after `improve_gate.sh`, `git status --porcelain` shows exactly the
    reverse staged, the routing object is unchanged, and the file count is 3;
  - a clean improve checkpoint (`--allow-empty`): nothing is reverted, nothing is
    written, and the routing is unchanged.
- `fabro validate`: unchanged counts (only a script changed).

## Depends on
Nothing. It can land first.

## Risk
`HEAD^` is the previous stage's checkpoint only if every stage commits. That holds today
(`--allow-empty`, AGENTS.md). If fabro ever skips empty checkpoints, this would revert the
previous stage. Pin it with a test that asserts the improve checkpoint is the only commit
being reverted, and put the assumption in the node comment.
