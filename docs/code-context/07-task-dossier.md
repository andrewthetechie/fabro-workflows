# 07 · `improve` writes the Task dossier; the stages after it read it

Read `00-overview-and-contracts.md` first. The contract for this task is C1. Decision 3: the
dossier is **optional permanently**: `improve_gate`
never checks it, and `ops/fabro-exploration-share.py` reports its presence rate every
time it runs.

## Why

`improve` already reads the current checkout to decide whether the task is `ready`. Then
`coder`, `rework_*` and `review` read the same files again. In one run, `improve`, `coder`
and `review_fix` each read `notification_sender.py` 8–9 times (ADR 0014, "Evidence").
The dossier hands on what `improve` found.

## Change

**`improve.md.j2`**: when the decision is `ready`, also write `/tmp/fabro/task-context.md`,
at most 6,000 bytes, with these headings:

```markdown
# Task dossier: <task id> <title>
## Files and symbols
- path:start-end  kind name: why it matters to this task
## Tests
- test file to extend: path
- command that runs it: <exact command>
## Governing text
- CONTEXT.md term or ADR path and heading, one line each
## Traps
- one line each, e.g. "`useSSE` is also imported by X; changing its signature breaks X"
```

Tell `improve` to fill `## Files and symbols` and `## Tests` from `fabro-code def`, `show`
and `tests`, and to cite line ranges that it has read. It must not write the dossier for
`redundant`, `split` or `needs_human`.

**`next_task`**: task 04 has already added `rm -f /tmp/fabro/task-context.md`.

**Consumers**: `coder.md.j2`, `rework.md.j2` and `reviewer.md.j2` get
`/tmp/fabro/task-context.md` as the second input, after the Repo map:

> `/tmp/fabro/task-context.md`: what the improve stage found about this task. Start from
> it. It can be missing or wrong. The checkout is the truth.

## Acceptance

- `fabro validate` gives the same baseline. `./ops/test-task-gates.sh` passes.
- On the first run after deploy, `task-context.md` is present for every `ready` task. Check
  with `fabro events <run> --json`: a `write_file` whose path is `task-context.md` in each
  `improve` visit.
