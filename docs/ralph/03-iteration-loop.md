# 03. The Iteration loop

## Outcome

A `ready` plan goes to `pick`. Each Iteration takes the first open Task on the rung its
misses earned, passes or misses through Task validation, and logs what happened. When every
Task has passed, or the loop runs out, `pick` routes to `final_merge`, which until task 06
is replaced by `stop_reason` (interim edge I2).

## Why

00: O6, O7, O17, O19, R2, R8, C3, C4.

## Change

Read `backlog`'s `next_task`, `excerpts`, `coder` (and the comments on `allow_partial` and
`max_retries`), `task_block_gate`, `autofix`, `validate`, `resolve_merge` and
`resolve_merge_gate`, and `invariants-backlog.md`, *Unmet criteria* and *Task validation*.

### Nodes

- `pick`: C3, in that order. POSIX `sh` and `jq`; prose in `//` comments above it (R6).
  Every branch prints one routing object carrying every key in C3.
- `excerpts`, `task_block_gate`, `autofix`, `resolve_merge`, `resolve_merge_gate`: copies.
  `resolve_merge_gate`'s `merge_return=task` target is `pick` here, not `improve`/`plan`.
- `iterate_t1` (`.coder`), `iterate_t2` (`.coder-t2`), `iterate_t3` (`.coder-t3`),
  `iterate_t4` (`.coder-t4`): `timeout="60m"`, `max_retries=1`, `allow_partial=true`, one
  prompt. The comment above them says the 60m is a new value: `backlog`'s `coder` is 180m
  for a whole task with its own dossier, an Iteration is meant to finish one planned Task,
  and `allow_partial` keeps a timed-out Iteration's work for the gate (O17). Re-derive it
  from the shakedown's Iteration times.
- `check`: `backlog.validate` plus C3's two replacements (`check_ok`).
- `record`: C4.

### Edges

```
plan_gate -> pick                    [condition="context.plan_status=ready"]          // replaces I1
pick -> resolve_merge                [condition="context.rebase_failed=true"]
pick -> excerpts                     [condition="context.loop=work && context.rebase_failed=false"]
pick -> stop_reason                  [condition="context.loop=done && context.phase=pre"]       // I2: task 06 → final_merge
pick -> stop_reason                  [condition="context.loop=exhausted && context.phase=pre"]  // I2
pick -> stop_reason
excerpts -> iterate_t1               [condition="context.rung=1"]
excerpts -> iterate_t2               [condition="context.rung=2"]
excerpts -> iterate_t3               [condition="context.rung=3"]
excerpts -> iterate_t4               [condition="context.rung=4"]
excerpts -> stop_reason
iterate_tN -> task_block_gate        [condition="outcome=succeeded"]
iterate_tN -> task_block_gate        [condition="outcome=partially_succeeded"]
iterate_tN -> record
task_block_gate -> autofix           [condition="outcome=succeeded && context.task_block=none"]
task_block_gate -> return_to_triage  [condition="outcome=succeeded && context.task_block=criterion"]
task_block_gate -> record
autofix -> check                     [condition="outcome=succeeded"]
autofix -> check                     [condition="outcome=failed"]
autofix -> stop_reason
check -> record                      [condition="outcome=succeeded"]
check -> record                      [condition="outcome=failed"]
check -> stop_reason
record -> pick
resolve_merge -> resolve_merge_gate  [condition="outcome=succeeded"]
resolve_merge -> resolve_merge_gate
resolve_merge_gate -> pick           [condition="context.merge_ok=true && context.merge_return=task", weight=10]
resolve_merge_gate -> resolve_merge  [condition="context.merge_attempts=1"]
resolve_merge_gate -> stop_reason
```

`excerpts` always succeeds, so its `stop_reason` edge only catches a `rung` of 0, which `pick`
never prints with `loop=work`. The `record -> pick` cycle runs through command nodes, which
is why the graph sets `loop_restart_signature_limit=100` (R1).

### `prompts/iterate.md.j2`

Start from `backlog/prompts/coder.md.j2` (its tool rules, the block contract, the git rule,
the `## Plan` paragraph). Then:

- You are one Iteration. You have no memory of earlier ones; `progress.md` is what they left.
- Work only `current_task.json`. Its `acceptance` is the definition of done. The issue is
  context, not scope.
- `task-context.md` is the plan for this Task; follow its `## Plan` steps in order and name
  every departure in your summary. Under `## Last attempt` is why the previous Iteration on
  this Task did not pass: start there.
- Run `task_check` before you finish. Submit `iteration` with `status: done` only when it
  passes and every acceptance statement holds; otherwise `progress`, saying what is left.
- `learnings` are facts about the repository that the next Iteration would want (a command,
  a trap, a convention), never a summary of your work.
- Never edit `tasks.json`, `progress.md` or anything under `/tmp/fabro/` except through
  `submit`.

## Acceptance

- `make check` passes, including `ops/test-task-gates.sh`.

## Tests

`ralph — pick` (real git, a scratch repository with a bare `origin`):

1. Three open Tasks, `misses` 0: `loop=work`, `rung=1`, `current_task.json` is the first.
2. First Task `misses` 3: `rung=2`; 6: `rung=4`; 8: that Task becomes `blocked`, the next
   open one is picked.
3. All `passed`: `loop=done`. One `blocked`, none open: `loop=exhausted`,
   `loop_exhausted` file written.
4. `iterations` = 9 with 3 Tasks: `exhausted`. 30 Tasks, `iterations` 24: `exhausted`.
5. Phase `post`, `post_iterations` 2: `exhausted`, both reason files written.
6. `misses` 1 and a `feedback/rework.md`: `task-context.md` ends with `## Last attempt` and
   the feedback.
7. `origin/main` moved with a conflicting change: `rebase_failed=true`, clean tree; phase
   `post`: no merge attempted.

`ralph — record`:

1. `check_ok` 1, `status: done`, receipt valid: `passed`, Task `status: passed`,
   `task_index` 1, `progress.md` gains `passed`.
2. `check_ok` 1, `status: progress`: miss. `check_ok` absent, `status: done`: miss.
3. No `iteration.json`: miss, progress line says `(no result submitted)`.
4. `task_block_scope.md` present: Task `blocked`, its text in `feedback/rework.md`.
5. Phase `post`: `post_iterations` advances, `iterations` does not.

`ralph — edges`: with the DOT reader, enumerate `loop`, `rung`, `phase`, `rebase_failed`,
and assert at most one conditional edge out of `pick` and `excerpts` matches each
combination `pick` can print.

## Depends on

02.
