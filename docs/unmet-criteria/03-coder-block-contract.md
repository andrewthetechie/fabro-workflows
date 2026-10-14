# 03: the coder blocks through a contract (U5, U6, C2)

## Changes

1. `_io/schemas/task_blocked.schema.json` (C2). Add `output: {path: /tmp/fabro/task_blocked.json,
   schema: task_blocked}` to the `coder` and `rework_t1`..`rework_t4` stages in
   `_io/manifest.json`. Regenerate. `cargo test` in `ops/fabro-io/` compiles the schema.
2. `backlog/prompts/coder.md.j2`:
   - Remove "implement the closest form that meets its intent and name the difference in your
     completion note". An acceptance criterion that cannot pass as written is a block.
   - *Inputs*: `/tmp/fabro/` stays read-only to the coder; `submit` is the one way to write
     there.
   - *Completion*: replace the `<blocked>` steps. Change nothing, call `submit` with
     `kind: "criterion"` (the criterion cannot be met as written: a non-goal, a constraint or
     the code forbids it) or `kind: "scope"` (it cannot be done without files outside the task),
     then emit `<promise>COMPLETE</promise>`. Never ship a similar behaviour in its place, and
     never relabel or reword one.
   - *Deliverable*: say the one exception (the block).
3. `backlog/prompts/rework.md.j2`: the same Completion change. Its "emit `<blocked>`" lines
   become the `submit` block.
4. `backlog/workflow.fabro`:
   - `task_block_gate` (C2): command node with `output_schema="routing"` and a comment above it.
     Every branch prints `task_block` and `triage_questions`.
   - Edges: replace `coder -> autofix` (both) and `rework_tN -> autofix` (four) with edges to
     `task_block_gate` with the same conditions. Add
     `task_block_gate -> autofix [condition="context.task_block=none"]`,
     `task_block_gate -> return_to_triage [condition="context.task_block=criterion"]` and the
     unconditional `task_block_gate -> rescue_brief`.
   - `next_task`: delete `/tmp/fabro/task_blocked.json` at the start of each task.
   - `rescue_brief`: when `task_block_scope.md` exists, it is the cause line. Delete it where
     `rescue_brief` deletes its other per-stop files.

## Tests (`ops/test-task-gates.sh`)

`task_block_gate`, with a valid receipt (`mkreadok`):

- no file: `task_block=none`;
- a file from an older visit: `none`, and the file is deleted;
- `criterion`: `task_block=criterion`, `return.json` has `source: "coder"`, and
  `triage_questions` names the criterion;
- `scope`: `task_block=scope`, and `task_block_scope.md` holds the reason;
- `kind=criterion` with an empty criterion, or a bad `kind`: `scope`.

Parsed edges: no edge from `coder` or `rework_t*` goes straight to `autofix`.

## Done when

`make check` passes.
