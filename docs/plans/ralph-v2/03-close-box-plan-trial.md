# Task 03: close the box-plan trial

Read `00-overview-and-contracts.md` first (E18, E19). Read `docs/plan-box-trial/00-overview.md` and
`docs/adr/0030-hosted-planner-replaces-improve.md`. **Invoke `/fabro-workflow`.**

## Steps

1. **Stylesheets**, `.fabro/workflows/ralph/workflow.fabro` and `.fabro/workflows/backlog/workflow.fabro`:
   replace the `{% if %}` ... `{% endif %}` block by one line,
   `.plan             { model: {{ inputs.coder_pool | default('coders-a') }}; reasoning_effort: medium; }`.
   Rewrite the stylesheet comment's `.plan` entry in both: the planner runs on the run's box since the
   trial closed (ADR 0034 D5), and the trial's record is `docs/plan-box-trial/result-2026-11-24.txt`.
   In `backlog`'s comment near the pre-review arm ("Three trials, three bits"), say the plan trial's bit
   is free since it closed, and keep the rest.
2. **Manifest**: in `_io/manifest.json`, the `plan` stage's `"box": "plan-arm"` becomes `true` in
   `ralph` and `backlog`. `python3.11 ops/fabro-io-manifest.py generate`. R18 now still lists `plan`.
   **Keep the `plan-arm` value itself** in the generator, the checker, `fabro-io` and the scheduler:
   finished runs and old images still carry it.
3. **Timeouts**: leave `plan` at 80m (ralph) and the current value (backlog). In each comment, replace
   the sentence that sizes the node for two arms with: it runs on the box only since the trial closed;
   the number is still the two-slot doubling of 2026-11-11 and unmeasured.
4. **`docs/plan-box-trial/result-2026-11-24.txt`**, plain text: the window (2026-10-28 to 2026-11-24);
   the arm rule; the merge numbers below; that the two windows before and after 2026-11-12 disagree and
   the later one is read; that the box arm is no worse, so the credit saving decides (the trial's own
   rule); and that the operator closed it (E19). Set the overview's **Status** line to
   `closed 2026-11-24: the box wins (ADR 0034 D5), result-2026-11-24.txt`.

   | `ralph` issue runs | box arm, merged | hosted arm, merged |
   |---|---|---|
   | 2026-11-02 to 11-12 | 16 of 61 | 25 of 66 |
   | 2026-11-12 to 11-24 | 50 of 97 | 44 of 87 |

5. **Tests and docs that name the arm in these two graphs.** Update
   `ops/scheduler/tests/test_workflow_version.py` (the assertion that `plan` reads `plan-arm`),
   `ops/tests/test_fabro_io_manifest.py` (the two expected tables that list `"plan": "plan-arm"` for the
   real source), and the box-stage row of `docs/agents/invariants-graph.md` and the `plan` row of
   `docs/agents/invariants-ralph.md`. Tests of the `plan-arm` mechanism on synthetic input stay.
   In `docs/README.md`, set the box-plan row's status to closed.
6. `ops/scheduler/src/fabro_scheduler/measure.py`: `planner_visits` keeps grouping by the arm bit. Add
   one sentence to its docstring: for runs dispatched after the trial closed the bit is not an arm.

## Done when

- `make check` passes; R13 and R18 report nothing.
- `grep -rn "issue_number // 2" .fabro` is empty.
- Commit: `feat(plan): the box plans in ralph and backlog; the box-plan trial is closed (ralph-v2 03)`.
