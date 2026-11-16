# Task 05: scheduler: the `sharpen` lease

Read `00-overview-and-contracts.md` first (D1–D4, D12, D20; C11, C12). Read `dispatch.py` whole,
`lease.py`, `reconcile.py` (`reconcile_leases`, `_requeue`, receipt recovery), `background.py`
(`fill`, `evict`, `_gap_minutes`, `make_room`, the `-bg` row mapping at `:630-637`), `fabro.py`
(`dispatch`, `dispatch_job`), `calibration.py` (`read_record`), and
`docs/plans/more-local-inference/06-pr-doctor.md` step 6 (the `pr` lease, the precedent).

## Goal

The scheduler dispatches one sharpening run at a time, ahead of every issue run, on a free
undrained coder pool, and records how it ended.

## Steps

1. **`issue-sharpen` as a coder loop.** Add it to `config.CODER_LOOP_WORKFLOWS` and to
   `workflow_version.ENTRYPOINTS`. Check every user of `CODER_LOOP_WORKFLOWS` (`_gap_minutes`,
   `evict`, the queue page) and make it handle a loop with no gap-table rows. The gap is 0, as
   for `pr-review` today (#78).
2. **Dispatch** (`DispatchLoop.tick`):
   - Before the issue loop, call `_dispatch_sharpen(now)`. It does nothing when a lease with
     `target_kind='sharpen'` is active, or when no `sharpen` row is `queued` with
     `next_eligible_at` ≤ now.
   - Otherwise it takes the eligible row whose issue ranks highest in the coder queue's order
     (repo priority, then issue number, as `queue.rank` gives it, D3), and the first free
     undrained pool, using soft affinity as `_pick_one` does.
   - The peak-hours pause must still let it run (D4): call it from `_paused` when the pause is
     the peak block, not when it is the disk gate.
   - When the box has a background run, `make_room` runs first, as for an issue dispatch.
3. **`_dispatch_sharpen`** creates the run with `FabroClient.dispatch_job` (C12 inputs and
   labels; `model_pool` from the lane's box→`-bg` mapping, so `coders-b` maps to `coders-a-bg`).
   - It writes **no** issue labels.
   - It acquires a `Lease` with `target_kind='sharpen'`, `workflow='issue-sharpen'` and
     `box_stages` from the manifest, as `_dispatch` does.
   - It sets the row to `running` with `run_id`.
   - A dispatch failure before a run exists leaves the row `queued`, with `next_eligible_at`
     5 minutes on. A `RunNotStarted` counts as a failed attempt.
   - It logs one line in the `dispatch:` style.
4. **Release** (`reconcile_leases`). For a `sharpen` lease:
   - On a terminal run, read C11 from the run's `record` stage (`calibration.read_record`'s
     method).
   - Succeeded with an outcome: write the row's terminal state and fields, remove
     `precheck-unverifiable`, and write a `run_history` row with `target_kind='sharpen'`.
   - Failed, cancelled by anything but the scheduler's own drain cancel, or lost: `attempts + 1`.
     Below 3, set the row back to `queued` with `next_eligible_at = now + 30 min`. At 3, set
     `failed` and remove the label (D12).
   - A fabro restart (`terminated`) requeues without counting, as the lane does.
   - No `agent`, `agent-in-progress` or receipt write, and no issue requeue. Read `_requeue` and
     the receipt recovery and confirm by test that a `sharpen` lease reaches neither.
5. **Drain and cancel.** `POST /api/pools/{pool}/cancel` on a pool holding a sharpen lease
   cancels the run. The row returns to `queued` without counting an attempt. The queue page's pool
   table shows the lease as `sharpen #N`.
6. **`/api/pools` and `/api/queue`.** The lease shows with its `target_kind`. The queue page gains
   a *Sharpening* strip: the active run, then the queued rows in dispatch order, then the last
   ten finished rows with their outcome.
7. **Tests:**
   - one active lease at a time;
   - dispatched before an issue in the same tick, so a free pool takes the sharpen lease and the
     next free pool takes the issue;
   - coder-queue order across repos;
   - no dispatch while a lease is active;
   - dispatch during the peak block, none under the disk gate;
   - the `-bg` mapping for each of the four pools;
   - no issue label writes on dispatch or release;
   - release for each outcome;
   - retries: the 30-minute spacing, the third failure, `terminated` not counted, the operator's
     cancel not counted;
   - receipt recovery and diversity ignore the lease;
   - the issue-run metrics (`store.py` queries on `target_kind='issue'`) do not count it;
   - `ENTRYPOINTS` ⊇ the dispatched workflow.

## Done when

- `uv run pytest` and `make check` pass.
- The commit is `feat(scheduler): the sharpen lease, ahead of issue runs and one at a time (issue-sharpening 05)`.
