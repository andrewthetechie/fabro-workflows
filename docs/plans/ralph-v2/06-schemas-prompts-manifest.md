# Task 06: schemas, prompts and the manifest stages

Read `00-overview-and-contracts.md` first (C2, C3's two tables, C5, C6). Read
`docs/agents/invariants-graph.md` *Stage I/O*.

The stages have no graph nodes until tasks 07 and 08, and the generator's rule 1 rejects a manifest
stage without an agent node. So this task adds the schemas, the prompts and the `iterate` change; tasks 07
and 08 add their manifest stages **from this file's text**.

## Steps

1. Copy `assets/schemas/*.schema.json` to `.fabro/workflows/_io/schemas/` unchanged.
2. Copy `assets/prompts/*.md.j2` to `.fabro/workflows/ralph-v2/prompts/` unchanged.
3. `python3 docs/plans/ralph-v2/assets/iterate-replacements.py .fabro/workflows/ralph/prompts/iterate.md.j2 .fabro/workflows/ralph-v2/prompts/iterate.md.j2`.
4. Manifest: `iterate_t1`..`t3` of `ralph-v2` use output schema `iteration_v2` (the path stays
   `/tmp/fabro/iteration.json`) and gain `/tmp/fabro/task_review/` and `/tmp/fabro/task_plans/` in their
   Sealed lists, as C3's table says. `resolve_merge`, `resolve_base`, `learn`, `standards`, `spec`,
   `local_standards`, `local_spec` and `refute` gain what C3's table says. `generate`.
5. `ops/fabro-io-manifest.py` rule 6 still holds (the three Iteration stages are equal but for `box`).

## The manifest stages (for tasks 07 and 08)

Every `about` is one short sentence. `live` is `true` and `also` is `[]` for all.

| Stage | Task | `prompt` | Inputs: name -> path, `about` | Output | `box` |
|---|---|---|---|---|---|
| `task_plan` | 07 | `task_plan.md.j2` | `current_task.json` R "The Task to plan."; `task-context.md` R "The issue planner's dossier for this Task."; `issue.json` R "The parent issue."; `progress.md` "What earlier Iterations of this run did."; `error.txt` -> `task_plan_error.txt` "Why the gate rejected the previous plan." | `task_plan.json`, `task_plan` | true |
| `task_refute` | 08 | `task_refute.md.j2` | the six of C3, below | `task_review/out/refute.json`, `task_refute` | true |
| `task_refute_fb` | 08 | same file | same | same | false |
| `task_review` | 08 | `task_review.md.j2` | the same six | `task_review/out/review.json`, `task_review` | true |
| `task_review_fb` | 08 | same file | same | same | false |

The six: `task.json` R "The Task: title, body, acceptance and source."; `plan.json` R "Goals, edge cases,
class of the bug and risks from the Task's plan."; `task.patch` R "Everything this Task changed.";
`check.json` R "What the mechanical gate ran."; `delta.patch` "What changed since the last review.";
`history.md` "Earlier findings and what the coder answered." Paths are `/tmp/fabro/task_review/in/<name>`.
Sealed lists: C3's table.

## Tests

- `cargo test` in `ops/fabro-io` compiles the new schemas (`CHECK_CARGO=always make check`).
- `ops/gate_tests/test_ralph_v2.py`: the iterate prompt equals the script's output run on ralph's current
  prompt (so a later v1 fix forces a decision); the three new prompts equal the assets byte for byte; none
  names a `/tmp/fabro` path; each holds `Only a result written by \`submit\``.
- `test_receipts.py`'s guard test: with the generated `ralph-v2` manifest, `iterate_t1` is blocked from
  `/tmp/fabro/task_review/state/findings/t1.json`, and `refute` from `/tmp/fabro/review/standards.json`
  (skipped without `fabro-io`, as today).

## Done when

- `make check` with `CHECK_CARGO=always` passes. Counts are unchanged (58/151).
- Commit: `feat(ralph-v2): schemas, prompts and sealed lists (ralph-v2 06)`.
