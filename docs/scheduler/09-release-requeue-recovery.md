# Release, requeue and recovery

## Tracer-Bullet Outcome
A run finishes and its box picks up the next item within 15 seconds. Kill the
fabro container mid-run and the scheduler notices its lease is dead, un-labels the
issue, puts it back in the queue with its original wait time, and dispatches it
again. Restart the scheduler itself and it rebuilds its lease state from GitHub.

## User Story
As the operator, I want the factory to keep going through a fabro restart or a
scheduler restart without me re-labelling issues by hand, so that an infrastructure
hiccup costs minutes rather than a morning.

## Description
Closes the loop draft 08 opened. Three mechanisms:

**Release** — poll fabro every 15s; a terminal run frees its lease.
**Requeue** — infra-shaped failures go back in the queue; agent-shaped ones do not.
**Recovery** — on startup, reconcile leases against fabro and GitHub.

After this draft the scheduler is safe to leave running unattended.

## Context Pack
- Source decisions: overview decisions 8 (poll fabro every 15s), 10 (requeue on
  infra-shaped failures only), 12 (release on terminal state only), and the
  Release/Requeue/Recovery contracts.
- Repo facts: **a fabro restart fails every in-flight run by design** —
  `reconcile_incomplete_runs_on_startup` walks `Runnable | Starting | Running |
  Blocked | Paused | Removing` and appends a failure event to each
  (`lib/apps/fabro-server/src/server.rs:3101-3139`). Runs in `submitted` are
  untouched. This is the canonical infra-shaped failure.
- Non-goals: drain, cancel, reordering (draft 11). Per-stage leasing — ADR 0006
  explains why a lease is not released when a run merely blocks on a human gate.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/reconcile.py
  ops/scheduler/src/fabro_scheduler/lease.py       (add release)
  ops/scheduler/src/fabro_scheduler/app.py         (recovery on startup)
  ops/scheduler/tests/test_reconcile.py
  ```

- Interfaces and names:

  ```python
  TERMINAL = frozenset({"succeeded", "failed", "dead"})
  INFRA_CATEGORY = "transient_infra"

  def is_terminal(run: dict) -> bool:
      return run.get("lifecycle", {}).get("status", {}).get("kind") in TERMINAL

  def should_requeue(run: dict) -> bool:
      """True only for infra-shaped failure. Reads the failure category fabro
      reports; a missing category is NOT infra-shaped."""

  def recover(leases, fabro_client, github_client, config) -> RecoveryReport: ...
  ```

  Those three are the whole terminal set (`00-overview-and-contracts.md`, finding 9).
  There is no `cancelled` kind and no `errored` kind — a cancel arrives as `failed`
  with `reason: "cancelled"` — and `dead` has to be in the set or a dead run holds
  its lease forever.

- Verified external contracts (observed live on `10.10.0.32` on 2026-09-18):

  `GET /api/v1/runs/{id}` terminal shape:

  ```json
  {"lifecycle": {"status": {"kind": "succeeded", "reason": "completed"},
                 "error": null, "archived": false}}
  ```

  `.lifecycle.status.kind` observed values: `submitted`, `runnable`, `running`,
  `blocked`, `succeeded`, `failed`. **`cancelled` is not one of them** — a cancel
  reports `kind: "failed"` with `reason: "cancelled"`.
  **`.lifecycle.queue_position` is always `null`** in production
  (`lib/components/fabro-store/src/run_state.rs:1212`) — never read it.

  A cancel is the one terminal failure that leaves work behind, and it does so
  silently. It exists before the graph's terminal label work, so the issue keeps
  `agent-in-progress`; `acquire` lists by `--label agent` and filters that label out,
  so the issue is invisible to every later run — not queued, not stuck, not reported.
  `recover`'s GitHub pass is the only thing that restores `agent`. Observed on this
  deployment by cancelling run `01M2V80N42R3V08SWHPNQBY81J`, whose jelly-swipe issue
  had to be relabelled by hand.

  The failure `category` for a cancel is **`canceled`, one L**, while the reason is
  `cancelled`, two Ls. Both are in the event tail (finding 10), and the requeue
  predicate keys on the category — so matching `"cancelled"` there matches nothing.

  Failure category, from a real timed-out stage on run
  `01M2SBJG92TX0DBRKY5881VM34`:

  ```json
  {"failure": {"message": "handler timed out after 5400000ms",
               "category": "transient_infra",
               "system_actor": "timeout"},
   "timing": {"wall_time_ms": 5400005}}
  ```

  That shape appears on `stage.failed` events, reachable via
  `GET /api/v1/runs/{id}/events?limit=1000&since_seq=N&order=asc`. The events list
  is **paginated with `since_seq`**; `limit` alone silently truncates at 1000 and
  `meta.has_more` tells you to continue.

  A run killed by a fabro restart is `failed`; confirm the category from its last
  `stage.failed` event rather than assuming.

  `POST /repos/{owner}/{repo}/issues/{n}/labels` and
  `DELETE /repos/{owner}/{repo}/issues/{n}/labels/{name}` as in draft 08;
  `DELETE` returning `404` is success.

- Behavior rules:
  - Poll fabro every **15s**. A lease whose run is terminal is released.
  - A lease whose `run_id` fabro returns `404` for is released and requeued —
    that is the "fabro lost it" case.
  - **Requeue** = remove `agent-in-progress`, add `agent`, and restore the item
    with its **original `first_seen`** so the starvation ceiling is not reset. That
    value is no longer in `issue_cache` by the time you need it — the poll deletes
    the row within a minute of dispatch, because the issue has left the `agent`
    collection — so read `lease.queued_since`, draft 08's one addition to the drafted
    lease schema (`docs/scheduler/08-lease-and-dispatch-loop.md`, *Corrected during
    implementation, 2026-09-19*). It is nullable, and `None` means "fall back to
    now".
  - **Do not requeue** an agent-shaped failure. The issue keeps whatever the run
    left on it (`agent-stuck` from `mark_stuck`, or nothing from `close_noop`)
    and a human re-arms it.
  - **Recovery on startup**, in this order:
    1. read lease rows from SQLite;
    2. for each, `GET /runs/{id}` — terminal or 404 releases it, applying the
       requeue rule;
    3. then scan GitHub for issues labelled `agent-in-progress` with **no** live
       lease, un-label them and requeue. This is the case where the scheduler
       died between labelling and recording the lease.
  - Recovery runs to completion **before** the dispatch loop starts, or it will
    dispatch onto a box it has not yet reconciled.
- Error and security rules: recovery must be idempotent — running it twice
  changes nothing the second time. Log every release and requeue with run id,
  repo and issue so the deployment log can reconstruct what happened.

## Acceptance Criteria
- [ ] `uv run pytest` passes.
- [ ] A run reaching a terminal state frees its box, and the next item dispatches
      within 15s.
- [ ] `docker compose restart fabro` mid-run: the scheduler detects the failed
      run, requeues the issue (`agent` restored, `agent-in-progress` gone) and
      redispatches it.
- [ ] A run ending at `mark_stuck` is **not** requeued; the issue keeps
      `agent-stuck` and does not reappear in the queue.
- [ ] Restarting the scheduler with a live run adopts the existing lease rather
      than double-dispatching.
- [ ] An issue left `agent-in-progress` with no lease is un-labelled and requeued
      on the next start.
- [ ] A requeued item keeps its original wait time — its position relative to the
      ceiling does not reset.

## Test Expectations
Framework: **pytest**, `uv run pytest` from `ops/scheduler/`. fabro and GitHub
faked with `respx`; SQLite in `tmp_path`.

Concrete case in `tests/test_reconcile.py`:

```python
INFRA = {"lifecycle": {"status": {"kind": "failed"}},
         "_last_failure": {"category": "transient_infra"}}
AGENT = {"lifecycle": {"status": {"kind": "failed"}},
         "_last_failure": {"category": "agent"}}

def test_only_infra_failures_requeue():
    assert should_requeue(INFRA) is True
    assert should_requeue(AGENT) is False
    assert should_requeue({"lifecycle": {"status": {"kind": "failed"}}}) is False  # missing == not infra

def test_requeue_preserves_first_seen(store):
    original = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    store.upsert_issue(Issue("o/a", 7, "t", frozenset({"agent"}), first_seen=original))
    store.requeue("o/a", 7)
    assert store.get_issue("o/a", 7).first_seen == original
```

## Dependencies
- Blocked by: "Lease state machine and the dispatch loop"
- Why blocked: needs the lease table, the dispatch loop and the label writes to
  have something to release, requeue and reconcile.
- Blocks: "Reorder, drain and cancel in the web UI"

## Labels
`feature`, `ops/scheduler`, `priority:high`

## Estimate
Large

## Risk
4 - the requeue path writes GitHub labels automatically. A wrong `should_requeue`
either loops a broken issue forever or silently drops work. The "missing category
is not infra" default fails toward dropping rather than looping, which a human
notices.

## Validator Stopping Point
`uv run pytest` green; a fabro restart mid-run observed to requeue and
redispatch; a `mark_stuck` run observed **not** to requeue.
