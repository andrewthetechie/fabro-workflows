# 02 · The `plan` agent and `plan_gate`

Read `00-overview-and-contracts.md` first. The contracts for this task are C1, C2 and C4.
Invoke `/fabro-workflow` first.

## Prompt: `_shared/triage/prompts/plan.md.j2` (new)

Inputs, in this order:
1. `/tmp/fabro/issue.json` (the improved body);
2. `/tmp/fabro/triage.json` and `/tmp/fabro/triage.md` (the verdict, the decisions, the
   acceptance boundary);
3. the checkout, read-only.

If `/tmp/fabro/repomap.md` exists (ADR 0014), it is the first input, and `fabro-code` is
available.

The prompt says:

- **What a task is.** Use the same size rules as `backlog/prompts/decompose.md.j2`'s
  "Task size budget": one implementation stage, one or two `covers`, and "add the module,
  then migrate callers" is two tasks. Copy the measured numbers (24 min median, 47 min p90),
  so that the two prompts agree on what one task means. Use ids in the same form as
  `decompose`'s `id` rule, because `decompose` reuses them.
- **The split rule**, stated as the gate enforces it (C1 rules 2–4). Do not invent a
  threshold. When there are 5–12 tasks, group the tasks into 2–3 children. Each child must
  land and leave `main` green on its own. Put the prerequisites in earlier children, and
  set `after` to the child that each one truly needs.
- **Read-only.** Do not run `gh` or `git`, and write only `plan.json`. The same boundaries
  as `triage.md.j2`.

## Graph: `_shared/triage/triage.fabro`

- `plan [class="plan", prompt="@prompts/plan.md.j2", timeout="30m", max_retries=1]`.
- `plan_gate [shape=parallelogram, output_schema="routing"]` implements C1:
  - It publishes `plan_status` (`map | split | too_large | none`) and `task_count`.
  - On `map`/`split`: it writes `plan_ok` and renders `task_map.md` (C2). The header takes
    `git rev-parse --short HEAD` and `date -u +%F`.
  - On `split`: it writes `/tmp/fabro/split_children.json`. That is `children`, with the
    task objects resolved and each child's own `task_map.md` fragment, ready for
    `apply_split`.
  - On `too_large`: it rewrites `triage.json` and `triage.md` as C4 says.
  - An invalid file on attempt 1 exits 1, with the usual "rewrite it exactly per the
    contract" line. On attempt 2 it exits 0 with `plan_status=none`.
- `claim`: add the C1 deletions and `plan_attempts`, and add `plan_status:\"none\"` to
  **both** of its routing objects.
- Edges:

  ```
  triage_gate -> plan            [condition="outcome=succeeded && context.triage_readiness=ready"]   (replaces -> apply_ready)
  plan -> plan_gate              [condition="outcome=succeeded"]
  plan -> apply_ready            (fail-open: the agent failed)
  plan_gate -> plan              [condition="outcome=failed"]
  plan_gate -> apply_ready       [condition="outcome=succeeded && context.plan_status=map"]
  plan_gate -> apply_ready       [condition="outcome=succeeded && context.plan_status=none"]
  plan_gate -> apply_split       [condition="outcome=succeeded && context.plan_status=split"]
  plan_gate -> post_questions    [condition="outcome=succeeded && context.plan_status=too_large"]
  plan_gate -> release
  ```

  The condition grammar has no parentheses, and `&&` binds tighter than `||`. So the
  `map`/`none` case is two edges, not one `&& (… || …)`.
  `plan -> apply_ready` is an **agent's** unconditional edge that goes to a success
  path, and that is deliberate: ADR 0015 D1 makes `plan` fail-open. Say so in a `//`
  comment. The command-node invariant ("unconditional edge lands on the failure node")
  still holds for `plan_gate`.
- Stylesheets: add `.plan { model: high-reasoning; }` to **both**
  `issue-triage/workflow.fabro` and `arch-review/workflow.fabro`.

## Tests (`ops/test-task-gates.sh`, triage section)

Add `plan_gate` to the extracted nodes. Fixtures:

- 3 tasks → `map`, and `task_map.md` has 3 numbered entries and the header;
- 7 tasks in 2 children → `split`, and `split_children.json` has 2 entries with their
  resolved tasks;
- 13 tasks → `too_large`, and `triage.json` is now `needs_info` with 1 question;
- invalid cases, each a first attempt that exits 1:
  - a task in two children;
  - a child with 5 tasks;
  - `after` pointing forward;
  - a bad child title;
  - 6 tasks and `children: []`;
  - a duplicate id;
- the second invalid attempt → exits 0 with `none`;
- `claim` deletes the C1 files, and both of its routing objects carry `plan_status`.

## Acceptance

`fabro validate` for `issue-triage` and `arch-review` is clean, with the node counts up by
2 (`plan`, `plan_gate`). The routing checker is clean. The gate suite passes.
