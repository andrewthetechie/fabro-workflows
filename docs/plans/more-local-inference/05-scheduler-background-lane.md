# Task 05: the scheduler's labels and background lane

Read `00-overview-and-contracts.md` first (D4, D7, D8, D9, D12, D17; C6, C7, C8). Read
`ops/README.md` *The coder scheduler*, then `ops/scheduler/src/fabro_scheduler/dispatch.py`,
`probe.py`, `fabro.py` (`build_run_intent`, `dispatch`, `cancel_run`, `get_stages`, the active
run listing), `workflow_version.py`, `store.py` (the `leases`, `scheduler_flags` and
`pool_state` tables), `summary.py`, `usage.py` and `peak.py`, and `ops/docker-compose.yaml`'s
`scheduler` service. `peak.py` and the peak-hours override (6e19d7b) are the pattern for a
config knob, a page control and an API.

## Goal

The scheduler labels every run with its box and lane, and it fills a box's gaps with queued
background jobs without ever delaying a coder loop. This task adds the lane, eviction and the
`box-noop` job kind only. Real job kinds arrive in 06, 07, 08 and 10.

## Steps

1. **Labels (C6).** Every coder-loop dispatch sends `lane=coder`, `box=<pool>` in addition to
   today's labels. Update `test_fabro.py`.
2. **Config** (`config.py`), each with tests in `test_config.py` and a row in `ops/README.md`'s
   environment table:
   - `SCHEDULER_BACKGROUND`: `on` or `off`, default `off`. An unparsable value is a
     `ConfigError`.
   - `SCHEDULER_BG_GAP_S`: default 30.
   - `SCHEDULER_BG_SLOT_RESERVE`: default 3.
   - `SCHEDULER_BOX_HEALTH`: the same `pool=url` format as C3. Empty means no box is in the
     lane, and the lane does nothing.
   - `SCHEDULER_MAX_CONCURRENT_RUNS`: default 8, matching fabro's cap. A comment says to keep
     it equal to `server.scheduler.max_concurrent_runs`.
   - `SCHEDULER_BG_KINDS`: comma-separated job kinds whose producers run (C7); default every
     known kind. An unknown kind is a `ConfigError`. Later tasks add their kind to the known
     list.
3. **`ops/docker-compose.yaml`.** In the `scheduler` service's `environment:`, add each of the
   six as `${VAR:-default}`, the way `SCHEDULER_RALPH_SHARE` is written, with a comment.
   `SCHEDULER_BACKGROUND` defaults to **`off`** here too (D12). `SCHEDULER_BOX_HEALTH`
   defaults to the two boxes' `/health` URLs (C3). Without these lines the container never sees
   the variables, because the scheduler does not read `~/fabro/.env` as an env file.
4. **Store.** Add the `background_jobs` table exactly as C7 gives it, with methods to:
   - enqueue, idempotent on `(kind, repo, target)` while `queued` or `running`;
   - pick the next job for a box by C7's rule (priority, then age, among jobs that fit,
     with the cancelled-three-times rule);
   - record a start (`run_id`, `box`, `last_started_at`), a finish, a cancel (`cancels + 1`,
     back to `queued`) and a failure.
5. **Dispatching a job.** `build_run_intent` builds only an issue run today.
   - Add `build_job_intent(workflow_version_id, repo, environment_id, inputs, labels)`, which
     sends the job's `inputs_json` plus `coder_pool=<box>-bg`, and the labels
     `lane=background`, `box=<box>`, `job=<kind>`, `source=scheduler`.
   - `workflow_version.py`: extend `ENTRYPOINTS` with `box-noop` (later tasks add their
     packages), keeping the version cache keyed by `(sha, entrypoint)`.
   - The repo's `environment_id` comes from its `[[repo]]` row, as an issue dispatch's does.
   - No GitHub label writes: background jobs never touch `agent` / `agent-in-progress`.
6. **Box stage lookup.** Decide whether a coder loop's current node is a box stage from the
   loop's workflow manifest (C1):
   - read the generated `FABRO_IO_MANIFEST` from the loop's workflow version's
     `workflow.toml`. Record the version's commit sha on the lease at dispatch if it is not
     there already, so the lookup survives a scheduler restart;
   - cache it per `(sha, workflow)`;
   - for `"plan-arm"`, apply the arm rule to the lease's issue number.
7. **Fresh stage reads (D7).** For condition 3 and for eviction, call `get_stages(run_id)` at
   decision time. Do not use the probe's cache. A loop with no stage in `running` does not
   qualify for a start this tick.
8. **Box health.** A small client for strix-infer `/health`: 5 s timeout. Any failure reads
   as "not idle".
9. **Gap prediction.** A static table in `config.py`, keyed by `(workflow, node id)`, giving
   the predicted remaining gap in minutes.
   - Start it from the overview's *Facts*: give `("backlog", "review_merge.watch_checks")`,
     `("ralph", "watch_checks")` 30 min, every other hosted or command
     node of `backlog` and `ralph` 8 min, and any unknown key 0.
   - Task 12 replaces the numbers with measured ones before Phase 1 deploys.
   - With no coder loop on the box, the predicted gap is unlimited.
10. **The lane** (`dispatch.py`), run in `tick()` after the primary dispatch loop:
    - **Evict first (D17):** for each box whose coder loop's fresh current stage is a box
      stage, cancel every active run labelled `lane=background, box=<box>`. Log each eviction
      and count it on the page.
    - Then, for each box in `SCHEDULER_BOX_HEALTH`, apply C7's conditions 1-6 in that order,
      pick a job by C7's rule, dispatch it (step 5), and record the start.
    - Free slots (C7 condition 5) are `SCHEDULER_MAX_CONCURRENT_RUNS` minus the runs in any
      active status, `pending` included.
11. **Completion.** The release poller (`reconcile.py`) watches leases only. Add a small poll
    that finishes `running` jobs from fabro run status:
    - `succeeded` → `done`;
    - `failed` with reason `cancelled` → back to `queued`, `cancels + 1` (this covers a claim's
      cancel, an eviction, and a guard's self-cancel);
    - `failed` with reason `terminated` (a fabro restart) → back to `queued`, without counting a
      cancel;
    - any other failure → `failed`, with the reason. A guard that blocked its stage (C10 step 5
      fallback) lands here; it is rare and needs a look.
12. **Before each coder-loop dispatch (D8).** If the active-run count (step 10's definition)
    would leave fewer than 1 free slot for it, cancel the youngest background run first.
13. **Keep background usage out of coder accounting (C8).** `summary.CODER_MODEL_PREFIX`
    (`"coders-"`) also matches `coders-a-bg`. Check every place that sums usage by that prefix
    (`summary.py`, `usage.py`, the scorecard). Where background runs could be counted, exclude
    models ending in `-bg`, and add a test. Where only leased runs are summarized, say so in a
    comment.
14. **Page and API.**
    - `GET /api/background` lists jobs and per-box state.
    - The queue page gets a *Background* strip: the switch state, and per box the running job
      or why none can start (switch off, drained, loop in a box stage, loop between stages, box
      busy, slots, no job fits), and the evictions today.
    - Rebuild `app.css` with `build-css.sh` if new classes appear.
15. **`box-noop` producer.** `POST /api/background/box-noop` with a `repo` enqueues one
    `box-noop` job (`typical_box_minutes` 10), so task 12 can measure cancels. No automatic
    producer.
16. **Tests** (`ops/scheduler/tests/test_background.py`):
    - each C7 condition blocking a start on its own, including "between stages";
    - the pick order (priority, age, fit, cancelled-three-times);
    - eviction: a background run on a box whose loop is in a box stage is cancelled, and one on
      a box whose loop is in a hosted stage is not;
    - completion for `succeeded`, `cancelled`, `terminated` and other failures;
    - label and input shape of a job dispatch;
    - slot reserve, counting `pending`, and cancel-before-coder-dispatch;
    - the switch off;
    - the compose file carries the six variables with their defaults (a test that reads
      `ops/docker-compose.yaml`).

    Use an injected clock, a fake fabro client and a fake health client, as `test_dispatch.py`
    and `test_peak.py` do.

## Done when

- `uv run pytest` in `ops/scheduler/` and `make check` pass.
- With `SCHEDULER_BACKGROUND=off` (the default), the scheduler behaves as before, apart from
  the new labels.
- Commit: `feat(scheduler): box and lane labels; the background lane, off by default (more-local-inference 05)`.

Then do task 11. After task 11, **stop**: Phase 1 is built. Report to the operator, who runs
task 12, Phase 1.
