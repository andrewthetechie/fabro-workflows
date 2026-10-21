# 10. Scorecard, Retro and summary

## Outcome

The scheduler summarizes a `ralph` run as well as a `backlog` one. `--by-workflow` and
`GET /api/scorecard/workflows` print both arms side by side with repo × pool strata and the
`ralph` line. The Retro classifies both workflows.

## Why

00: O3, O21, O22, R7, R8, C12.

## Change

**Precondition P1.** Read `ops/scheduler/src/fabro_scheduler/` first: if `trial.py` still
exists, the plan trial has not closed. Stop and report.

Read `summary.py`, `collector.py`, `scorecard.py`, `retro.py`, `usage.py`, `prfacts.py`,
`docs/scorecard/README.md`, and `docs/factory-retro/00-overview-and-contracts.md` (C1's key
rules).

- `summary.py`: the node ids per workflow (C12). The workflow comes from `run_history`.
  `ralph`'s `refute_gate` publishes the same keys as the shared one (task 04), so the verdict
  parser is shared.
- `scorecard.py`: `by_workflow` and the strata (C12). The `ralph` line reads stage rows:
  Iterations are visits to `iterate_t1`..`t4`; Refute visits are `refute` visits; Tasks by
  `source` need `tasks.json`, which only the run has: the collector reads it once at release
  through `read_sandbox_file`, as the probe does, and stores the counts in the summary.
  `loop_exhausted` is the `pick` context key. `report_queued` hand-offs are the terminal node.
- `app.py`: `GET /api/scorecard/workflows`. `ops/fabro-scorecard.sh --by-workflow` calls it.
- `retro.py`: C12 and R7. The class key's node part is `ralph.<node>` for a `ralph` run; the
  key regex already allows `.`. The Discord text says how many runs of each workflow the week
  had.
- Hosted cost: the scorecard shows $0 for OpenAI and z.ai (the catalog has no cost rows,
  deployment log 2026-10-14). Do not add rows here. The `ralph` line prints Refuter tokens per
  run (input, cached, output) so task 12 can price them at luna's rates.

## Acceptance

- `uv run pytest` passes. `make check` passes. `docs/scorecard/README.md` documents the new
  view and the `ralph` line.

## Tests

- A window with 3 `backlog` and 2 `ralph` runs: two cards; the strata table has a row per
  repo × pool with each arm's counts.
- A `ralph` summary built from recorded stage rows and events: first edit read from
  `iterate_t1`, two `refute_gate` verdicts in order, Iterations 5.
- `retro.classify` over a `ralph` run that ended at `mark_stuck`: key
  `ending:ralph.mark_stuck:no-pr`; a `backlog` run's key is unchanged.

## Depends on

09.
