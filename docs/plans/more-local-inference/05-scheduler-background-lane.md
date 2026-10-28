# Task 05: the scheduler's labels and background lane

Read `00-overview-and-contracts.md` first (D4, D7, D8, D9, D12; C6, C7). Read `ops/README.md`
*The coder scheduler*, then `ops/scheduler/src/fabro_scheduler/dispatch.py`, `probe.py`,
`fabro.py` (`build_run_intent`, `dispatch`), `store.py` (the `scheduler_flags` and `pool_state`
tables) and `peak.py`. `peak.py` and the peak-hours override (6e19d7b) are the pattern for a
config knob, a page control and an API.

## Goal

The scheduler labels every run with its box and lane, and it fills a box's gaps with queued
background jobs without ever delaying a coder loop. This task adds the lane and the `box-noop`
job kind only. Real job kinds arrive in 06, 07, 08 and 10.

## Steps

1. **Labels (C6).** `build_run_intent` gains `lane` and `box` (and `job` for background).
   Every coder-loop dispatch sends `lane=coder`, `box=<pool>`. Update `test_fabro.py`.
2. **Config** (`config.py`), each with tests in `test_config.py` and a row in `ops/README.md`'s
   environment table:
   - `SCHEDULER_BACKGROUND`: `on` or `off`, default `off`. An unparsable value is a
     `ConfigError`.
   - `SCHEDULER_BG_GAP_S`: default 30.
   - `SCHEDULER_BG_SLOT_RESERVE`: default 3.
   - `SCHEDULER_BOX_HEALTH`: the same `pool=url` format as C3.
   - `SCHEDULER_MAX_CONCURRENT_RUNS`: default 8, matching fabro's cap. A comment says to keep
     it equal to `server.scheduler.max_concurrent_runs`.
3. **Store.** Add the `background_jobs` table (C7), with methods to enqueue (idempotent on
   `(kind, repo, target)` while `queued` or `running`), claim the next job for a box, record a
   start, and record a finish or a cancel. Requeue on cancel, with `cancels + 1`. A job
   cancelled 3 times needs a predicted gap of at least twice its `typical_box_minutes`.
4. **Box stage lookup.** The probe gives the coder loop's current `node_id`. Decide whether
   that node is a box stage from the run's workflow manifest (C1):
   - Read the generated `FABRO_IO_MANIFEST` from the registered workflow version's
     `workflow.toml`, which `workflow_version.py` already clones.
   - Cache it per version.
   - For `"plan-arm"`, apply the arm rule to the lease's issue number.
5. **Box health.** A small client for strix-infer `/health`: 5 s timeout. Any failure reads
   as "not idle".
6. **Gap prediction.** A static table in `config.py` maps node id to predicted gap minutes.
   - Start it from the overview's *Facts*: median 11 min overall. Give `review_merge.watch_checks`
     and `watch_checks` 30 min, every other hosted or command node 8 min, and any unknown node 0.
   - Task 12 replaces the numbers with measured ones.
   - With no coder loop on the box, the predicted gap is unlimited.
7. **The lane** (`dispatch.py`), run in `tick()` after the primary dispatch loop:
   - For each box, apply C7's conditions 1-6, in that order.
   - On a start, dispatch through the existing `FabroClient.dispatch` path:
     - `workflow` is the job's package;
     - inputs are `coder_pool=<box>-bg` plus the job's own inputs;
     - labels are `lane=background`, `box=<box>`, `job=<kind>`.
   - No GitHub label writes: background jobs never touch `agent` / `agent-in-progress`.
   - Record the run id on the job.
8. **Completion.** The release poller (`reconcile.py`) already watches leased runs. Background
   runs are not leases, so add a small poll that finishes `running` jobs from fabro run status:
   - `succeeded` → `done`;
   - `failed` with reason `cancelled` → back to `queued`;
   - any other failure → `failed`, with the reason.
9. **Before each coder-loop dispatch (D8).** If fabro's active-run count would leave fewer than
   1 free slot for it, cancel the youngest background run first.
10. **Page and API.**
    - `GET /api/background` lists jobs and per-box state.
    - The queue page gets a *Background* strip: the switch state, and per box the running job
      or why none can start (switch off, drained, loop in a box stage, box busy, slots, no job
      fits).
    - Rebuild `app.css` with `build-css.sh` if new classes appear.
11. **`box-noop` producer.** `POST /api/background/box-noop` enqueues one `box-noop` job for a
    repo, so task 12 can measure cancels. No automatic producer.
12. **Tests** (`ops/scheduler/tests/test_background.py`): each C7 condition blocking a start on
    its own, cancel and requeue counting, the cancel-three-times rule, label shape, slot
    reserve, cancel-before-coder-dispatch, and the switch off. Use an injected clock and a fake
    health client, as `test_dispatch.py` and `test_peak.py` do.

## Done when

- `uv run pytest` in `ops/scheduler/` and `make check` pass.
- With `SCHEDULER_BACKGROUND=off` (the default), the scheduler behaves as before, apart from
  the new labels.
- Commit: `feat(scheduler): box and lane labels; the background lane, off by default (more-local-inference 05)`.

**Stop here.** Phase 1 is built. Report to the operator, who runs task 12, Phase 1.
