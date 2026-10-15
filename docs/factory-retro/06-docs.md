# Task 06: docs

Read `00-overview-and-contracts.md` first.

## Steps

1. `docs/scorecard/README.md`, section **Not built**: replace the bullet "A weekly Discord
   line. `ops/fabro-scorecard.sh --last-week` prints the table to paste." with one saying
   that the weekly retro (`docs/factory-retro/`, ADR 0026) posts a scorecard summary to
   Discord every Monday and files issues for failure classes. Keep the other bullet.
2. `docs/README.md`: the `docs/factory-retro/` row exists already (written with the plan).
   Change its status from "Planned" to "Built 2026-MM-DD (tasks 01-06, local commits); task
   07, the deploy and the one-month check, is the operator's."
3. This folder's `00-overview-and-contracts.md`: set **Status** to the same.
4. Do not edit ADR 0026. The operator changes its status after task 07.
5. Read every file this series changed and check that no target repository's name appears
   in any text written for GitHub: templates, tests' expected bodies, docs examples. The
   test fixtures may name them as *configured* repositories; that is what the guard reads.

## Done when

`make check` passes. One commit: `docs(factory-retro): built, task 07 open (#33)`. Then
stop and report: the commits, the test counts, and anything in a task that did not match
the code.
