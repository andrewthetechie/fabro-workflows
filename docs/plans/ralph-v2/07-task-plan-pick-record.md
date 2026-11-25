# Task 07: the Task plan, `pick` and `record`

Read `00-overview-and-contracts.md` first (C1, C2, C6, *Files in /tmp/fabro*, *Edges*). **Invoke
`/fabro-workflow`.** Read `docs/agents/invariants-ralph.md` *The Task list* and *The loop*, and
`pick`, `record`, `excerpts`, `task_block_gate` and `plan_gate` in `ralph`'s graph.

After this task a passing Task goes to `reviewing` and `pick` publishes `loop=review`, but
`task_review_prep` does not exist. So in this task **`record` sets `passed`, not `reviewing`**, and
prints `review_due=false` always; and `pick` has no `loop=review` branch (C1 `pick` step 3 is task 08's).
Build everything else of C1 and C2. Task 08 turns the two on.

## Steps

1. `prep` (R10 row with one replacement): after `mkdir -p /tmp/fabro/review /tmp/fabro/feedback /tmp/fabro/extra`
   add the lines that remove and re-create `task_plans/`, `task_review/in`, `task_review/out`,
   `task_review/state/{patches,answers,findings,dropped,recovers,rounds}`, remove `task_plan.json`,
   `task_plan_error.txt` and `task_plan_tries`, and write `0` to `rework_iterations`.
2. `task_plan` and `task_plan_gate`, with C2's attributes, rules and edges. Manifest stage from task 06.
   R18's two hook lines gain `task_plan`; R11's `io-guard` matcher gains `task_plan`.
3. `pick` and `record` per C1. Both leave the R10 rows and join `R10_V2_OWN`; their `//` comments say what
   differs from v1 and cite C1.
4. The render program (C2) is written once and pasted into both nodes. Use `jq -r --slurpfile`; no
   `#` lines, no backslash other than `\"`.
5. `learn_prep` (R10 row with one replacement): its Task line gains
   `, reviews=<n>` and, when set, `, <block_reason>`; the highest-rung cap `4` becomes `3` (the
   expression occurs twice in one line; the replacement is the whole `jq -r` line).
6. `check-host.sh` baseline: `60 nodes, 156 edges`.

## Pitfalls

- `pick` merges `origin/main` before it writes `task_base_sha`. Do not move it: the patches of C1 must
  hold no mainline change.
- `excerpts` reads `task-context.md`, so `task_plan_gate` writes it before the edge to `excerpts`.
- `excerpts` reads `rung` from context: `task_plan_gate` must not publish it.
- `task_plan` writes `.io/stage.json`. `record` compares `iteration.json` to the visit that is current
  **after** the Iteration, as v1 does: nothing to change, but test it in order.
- `set -e` is not used in `pick` or `record` (v1). Keep it that way; a miss must never stop the run.
- `jq 1.6`: no `ltrimstr` on null, no `getpath` surprises, `any(g; cond)` with several outputs is false.

## Tests (`test_ralph_v2.py`)

Each runs the real scripts in their real order (`pick` -> `task_plan_gate` -> `excerpts` ->
`task_block_gate` -> `record` -> `pick`), with a real git repository and a fake receipt, as
`test_ralph.py` does:

- a valid plan; each rule of C2 step 1 broken alone (retry, with the right sentence in
  `task_plan_error.txt`), then broken again (fallback plan: 1, 3, 4 and 6 acceptance statements);
- a plan with another stage's receipt, and a missing file, twice;
- a Task with a stored plan goes straight to `work`; a `ci`, a `final_fix` and a `post` Task never plan;
- the render: goals open and done, edge cases, bug class, `## Last attempt`, and no empty heading;
  the program is the same text in both nodes;
- misses 0-5 give rungs 1,1,2,2,3,3 and 6 blocks with the reason; the cap blocks an `open` Task with
  `reviews` 0 and does not block one with `reviews` 1;
- `record`: a pass, a miss and a scope block each write the right patch file; an empty diff writes none;
  a Task with one missed and one passed Iteration has both patches; `goals_done` union; answers written
  only with `reviews` above 0; a dispute without evidence is not written; an id in both lists is disputed;
  `iterations` against `rework_iterations` against `post_iterations`;
- a mainline conflict still reaches `resolve_merge` with `task_step=none`;
- every key on every path of `pick`, `record` and `task_plan_gate`.

## Done when

- `make check` passes, and the v2 tests pass in the bookworm container. Counts are **60/156**.
- Commit: `feat(ralph-v2): per-Task box plan, pick and record (ralph-v2 07)`.
