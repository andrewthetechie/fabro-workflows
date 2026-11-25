# Task 10: scheduler routing

Read `00-overview-and-contracts.md` first (E2, C8). Read `dispatch.py` (`choose_workflow` and
`_dispatch`), `config.py` (`ralph_share`, the gap table, `CODER_LOOP_WORKFLOWS`), and
`workflow_version.py`'s `ENTRYPOINTS`.

## Steps (`ops/scheduler/src/fabro_scheduler/`)

1. `config.py`: `DEFAULT_RALPH_V2_SHARE = 0`, `ralph_v2_share: int` on `Config`, read by
   `_env_share(environ, key, default)`, which `_env_ralph_share` now calls too (same allowed values, same
   errors naming the key). `SCHEDULER_RALPH_V2_SHARE` is the key.
2. `dispatch.py`: `choose_workflow(issue, share, v2_share)` per C8, pure and total, with its docstring
   extended. `_dispatch` passes both. No other caller exists (`grep -rn choose_workflow ops`).
3. `workflow_version.py`: `RALPH_V2_ENTRYPOINT = "workflows/ralph-v2/workflow.fabro"` and its
   `ENTRYPOINTS` row.
4. `config.py`: `"ralph-v2"` in `CODER_LOOP_WORKFLOWS` and `BACKGROUND_GAP_WORKFLOWS`. Gap rows: copy
   every `("ralph", node)` row as `("ralph-v2", node)` with the comment `# copied from ralph, unmeasured`,
   except `refute`, `refute_gate` and `refute_prep` (their position moved), which get 1; add
   `("ralph-v2", "task_review_gate"): 1` and `("ralph-v2", "task_plan_gate"): 1`. Task 13 re-measures.
5. `summary.py`: `NODES["ralph-v2"]` = ralph's with `coder_nodes` of `iterate_t1`..`t3`. The comments that
   say "`ralph`'s" for `LEARN_PREP_NODE` and the loop head name both.
6. `collector.py`: `if workflow == "ralph"` becomes `if workflow in ("ralph", "ralph-v2")`.
7. `scorecard.py`: `by_workflow`'s two `("backlog", "ralph")` tuples become
   `("backlog", "ralph", "ralph-v2")`, so the arm has a column at zero. The `## ralph` section stays
   ralph-only; task 11 adds v2's. `retro.py`: the "arms" line lists the workflows of the same tuple that
   have runs in the window; its condition becomes "any of `ralph`, `ralph-v2`". `node_part` already
   prefixes any workflow but `backlog`; add a test, no code.
8. `ops/docker-compose.yaml`: after `SCHEDULER_RALPH_SHARE`,
   `SCHEDULER_RALPH_V2_SHARE: "${SCHEDULER_RALPH_V2_SHARE:-0}"` with a comment: 0 is the kill switch, 50
   the trial's parity, set in `~/fabro/.env`, `docker compose up -d --no-deps scheduler`. Without this line
   the variable never reaches the container. `ops/README.md`: its table row next to `SCHEDULER_RALPH_SHARE`.

## Tests

`test_dispatch.py`: the whole table of C8 (`share` 0/50/100 x `v2_share` 0/50/100 x even/odd x fast-path);
the lease, the run's `workflow` label and the history row of a v2 dispatch all read `ralph-v2`; a requeue
keeps its arm. `test_config.py`: default, 50, 100, 25, text. `test_workflow_version.py`: `ENTRYPOINTS` holds
every value `choose_workflow` can return (**the real mapping, not a fake**), and `read_box_stages` of the
real tree for `ralph-v2` lists `task_refute` and not `task_refute_fb`. `test_background.py`: a `ralph-v2`
lease in `task_refute` blocks its box; in `check` it predicts the copied gap. `test_collector.py`: task
sources for a v2 run. `test_scorecard.py`, `test_retro.py`: step 7.

## Done when

- `uv run pytest` in `ops/scheduler` and `make check` pass.
- `grep -rnE '"ralph"|=== ?.ralph' ops/scheduler/src` shows no place that should know both and does not;
  list in the report each hit you left and why.
- Commit: `feat(scheduler): route even issues to ralph-v2 behind a share that ships at 0 (ralph-v2 10)`.
