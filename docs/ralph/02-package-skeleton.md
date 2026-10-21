# 02. Package skeleton: claim, prep, plan and the exits

## Outcome

`.fabro/workflows/ralph/` exists and validates. A run claims its issue, preps the
workspace, plans, and routes the plan's disposition. A `ready` plan goes to `stop_reason`
until task 03 replaces that edge (interim edge I1).

## Why

ADR 0029 D1. 00: O4, O5, O16, R1, C1, C2.

## Change

Read `backlog/workflow.toml`, `backlog/workflow.fabro` from the graph attributes to
`route_lane`, and `decompose_gate`, `close_noop`, `return_to_triage`, `mark_stuck` and their
comments.

### `workflow.toml`

C1, all of it. Copy `backlog`'s sections and comments where C1 says "as in `backlog`", and
write a short comment above each fallback row saying why it differs from `backlog`'s (the
`kimi-k3` order is O5; `"glm-5.3"` has no `kimi` rung because Kimi's quota is the
reviewers'). Run `ops/fabro-io-manifest.py generate` to write `FABRO_IO_MANIFEST` and
`FABRO_AGENT_GUIDE`.

### `workflow.fabro`

- Graph attributes and stylesheet from C1 and *The graph*. Write the comment block above the
  stylesheet: each class, its model, and the decision it comes from.
- `claim`: a copy of `backlog.claim`.
- `prep`: `backlog.prep` with its counter lines replaced by:
  `echo 0` into `iterations`, `post_iterations`, `refute_visits`, `final_fix_attempts`,
  `plan_attempts`; `echo pre > /tmp/fabro/phase`; `rm -f /tmp/fabro/loop_exhausted
  /tmp/fabro/progress.md /tmp/fabro/remainder.json /tmp/fabro/remainder_issue
  /tmp/fabro/stuck_reason.md`. It also writes `/tmp/fabro/task-map.md`: the issue body's
  `## Task map` section when there is one (an `awk` over `issue.json`'s body), else nothing.
  `prep` is a C8 copy with replacements; record them in task 07's table (create the
  `R10_COPIES` table in task 07, and until then leave a `// R10:` comment above each copy
  naming its original and its replacements).
- `plan` (agent, `.plan`, `timeout="20m"`, `max_retries=1`). The comment above it gives the
  timeout's source: the plan trial's `plan` node, same provider family, same 20m reasoning
  (glm-5.3's 15m39s stall).
- `plan_gate`: C2. Model it on `decompose_gate`: the receipt lines, the attempt counter, the
  `QT=` line word for word, and a routing object on every path. Validate with `jq`: the
  disposition, `tasks` non-empty with unique non-empty ids for `ready`, `contradictions`
  non-empty with non-empty fields for `contradiction`.
- `close_noop`, `return_to_triage`, `mark_stuck`: copies of `backlog`'s. Where a copy names
  `human_rescue`, `rescue_brief` or a `backlog`-only file, record the replacement.
- Until task 08, `close_noop`, `return_to_triage` and `mark_stuck` go to `exit`.

### Edges

```
start -> claim
claim -> prep                        [condition="outcome=succeeded"]
claim -> stop_reason
prep -> plan                         [condition="outcome=succeeded"]
prep -> stop_reason
plan -> plan_gate                    [condition="outcome=succeeded"]
plan -> stop_reason
plan_gate -> plan                    [condition="outcome=failed"]
plan_gate -> stop_reason             [condition="context.plan_status=ready"]          // I1: task 03 sends this to pick
plan_gate -> return_to_triage        [condition="context.plan_status=contradiction"]
plan_gate -> close_noop              [condition="context.plan_status=redundant"]
plan_gate -> close_noop              [condition="context.plan_status=invalid"]
plan_gate -> mark_stuck              [condition="context.plan_status=needs_human"]
plan_gate -> stop_reason
return_to_triage -> exit             [condition="outcome=succeeded"]
return_to_triage -> mark_stuck
stop_reason -> mark_stuck            [condition="context.stop_phase=pre"]
stop_reason -> mark_stuck
close_noop -> exit
mark_stuck -> exit
```

`mark_stuck` must say why the run stopped. `backlog`'s `mark_stuck` relies on
`rescue_brief` having written `stuck_reason.md`, and `ralph` has no rescue gate (R1). So
every unconditional (failure) edge in the graph goes to `stop_reason`, a new routing command
node modelled on `rescue_brief`'s `failure_signature` reading (`stdin_source`, as R9
describes). It keeps a reason an earlier node already wrote, otherwise writes
`"<stage> failed (<class>)"`, never fails, and publishes `/tmp/fabro/phase` as `stop_phase`: `pre` →
`mark_stuck` with `stuck_reason.md`; `post` → `mark_needs_human` with both
`merge_block_reason` and `needs_human_reason` (task 07 adds that edge; until then `post`
cannot occur). Conditional edges straight to `mark_stuck` (a `needs_human` plan, C7's red
Final validation) write their own reason first.

### `prompts/plan.md.j2`

Start from `backlog/prompts/plan.md.j2` and `decompose.md.j2`. The plan covers the whole
issue, not one task:

- Read the issue, the Task map hint, the Repo map, and the code (`code_*` tools).
- Dispositions: when each applies. A Contradiction is a criterion a non-goal forbids
  (`CONTEXT.md`), and is never planned around.
- Tasks: ordered, each small enough for one coder session (one cohesive change, its tests
  included), at most 8 that matter most first. More than 8 are allowed and become the
  Remainder issue.
- `acceptance`: checkable statements, each traceable to the issue.
- `context`: `## Files and symbols`, `## Plan` (one step per edit with `path:N-M`, intent,
  and the criterion it serves; snippets ≤ 15 lines only for non-obvious logic; never a whole
  file), `## Done when`. `excerpts` copies every `path:N-M` it finds.
- Submit with the `submit` tool, never `write_file`.

## Acceptance

- `make check` passes: R1, R2, R4, R5, R6, R8 clean for `ralph`; `FABRO_IO_MANIFEST` has the
  `ralph` stages; `workflow.toml` parses.
- `ops/test-task-gates.sh` passes the new sections.

## Tests

`ralph — plan_gate`: a `ready` result with 3 tasks (tasks.json has 3 entries with
`status: open`, `misses: 0`, `task_index` 0); with 10 tasks (8 in tasks.json, 2 in
remainder.json); a duplicate id (invalid, attempt counter 1, exit 1); a second invalid
(routes `invalid`); `contradiction` (return.json and `triage_questions` match
`decompose_gate`'s for the same input); a result without this visit's receipt (invalid).
`ralph — prep`: `task-map.md` from a body with and without `## Task map`.

## Depends on

01.
