# 09. Scheduler routing

## Outcome

With `SCHEDULER_RALPH_SHARE=50`, the scheduler dispatches every even, non-`fast-path` Queue
item as a `ralph` run and the rest as `backlog`. With 0 (the default), nothing changes. Every
lease and `run_history` row records its workflow. `ops/fabro-fire-ralph.sh` fires one `ralph`
run by hand.

## Why

00: O2, O21, O23, C11.

## Change

Read `ops/scheduler/src/fabro_scheduler/{config,dispatch,fabro,workflow_version,lease,store,app}.py`
and their tests, and `ops/fabro-fire-backlog.sh`.

- `config.py`: `ralph_share` (C11). `ops/docker-compose.yaml` and `ops/README.md` name the
  variable; the compose file sets it to `0` explicitly so the switch is one visible edit.
- `workflow_version.py`: `RALPH_ENTRYPOINT`. `VersionCache` keyed by `(sha, entrypoint)`.
- `fabro.py`: `FabroClient.dispatch(..., workflow)`. It picks the entrypoint, registers the
  version for it, and adds `workflow` to the run labels.
- `dispatch.py`: `choose_workflow` (C11), pure, in its own small function with its own tests.
  `_dispatch` calls it once and passes the result on; the lease carries it.
- `lease.py`, `store.py`: the `workflow` column on `leases` and `run_history` (`ALTER TABLE
  ... ADD COLUMN ... NOT NULL DEFAULT 'backlog'` in the existing migration path). The release
  copies it.
- `app.py`: the workflow on `/` (the lease rows) and `/history`.
- `ops/fabro-fire-ralph.sh`: C11. Add it to `ops/deploy-host.sh`'s script list beside
  `fabro-fire-backlog.sh`.

Nothing in the requeue, reconcile, remainder or split code changes: a `ralph` run uses the
same labels and endings as a `backlog` run. Read `reconcile.py` and confirm it keys on
nothing `backlog`-specific (a node id, a file path); if it does, stop and report.

## Acceptance

- `uv run pytest` in `ops/scheduler/` passes. `make check` passes.

## Tests

- `choose_workflow`: share 0 → `backlog` for every number; share 50 → even `ralph`, odd
  `backlog`; even with `fast-path` → `backlog`; `Fast-Path` (not the label) → `ralph`.
- `load_config`: `SCHEDULER_RALPH_SHARE` absent → 0; `50` → 50; `25`, `yes`, `-1` →
  `ConfigError` naming the key.
- `VersionCache`: the same sha with two entrypoints registers twice; the same pair reuses.
- `_dispatch` with share 50 and an even item: the intent's version is the `ralph`
  entrypoint's, the labels carry `workflow: ralph`, the lease row's `workflow` is `ralph`,
  and the release writes it to `run_history`.
- A database created before this task opens, gains the columns, and its old rows read
  `backlog`.

## Depends on

Nothing in this series (the graph is not needed to test routing). It must not be deployed
before task 08 is on `main`.
