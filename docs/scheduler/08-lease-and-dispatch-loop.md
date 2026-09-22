# Lease state machine and the dispatch loop

## Tracer-Bullet Outcome
With both boxes free and work waiting, the scheduler dispatches the top-ranked
issue to `coders-a` and the next-ranked one from a *different* repo to `coders-b`,
labels both issues `agent-in-progress`, and the queue page shows two active leases.
No human involvement.

## User Story
As the operator, I want work to start on a free box by itself so that I stop
hand-firing runs and the coders stop idling between them.

## Description
The centre of the system. Joins draft 06's ranked queue to draft 07's dispatcher
through a lease table, and runs it on a loop.

Release, requeue and recovery are deliberately **draft 09** — this draft acquires
leases and never lets them go, which is a valid intermediate state: the boxes fill
up once and the operator restarts the service to clear them. Do not ship it to
unattended operation before 09.

## Context Pack
- Source decisions: overview decisions 1 (whole-run lease), 6 (one in-flight run
  per repo), 9 (scheduler sets `agent-in-progress` at dispatch), 11 (soft repo
  affinity). ADR 0006 for why the lease is whole-run.
- Repo facts: `CONTEXT.md` defines **Coder instance**, **Coder lease**, **Queue
  item**, **Drain**. Use those words in code and UI.
- Non-goals: releasing a lease, requeueing, recovery after a crash, drain, cancel,
  the graph change that makes `issue_number` required.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/lease.py
  ops/scheduler/src/fabro_scheduler/dispatch.py
  ops/scheduler/src/fabro_scheduler/store.py      (add the leases table)
  ops/scheduler/src/fabro_scheduler/app.py        (start the loop on startup)
  ops/scheduler/tests/test_dispatch.py
  ```

- Interfaces and names:

  ```python
  @dataclass(frozen=True)
  class Lease:
      coder_pool: str        # "coders-a" | "coders-b"
      repo: str
      issue_number: int
      run_id: str
      dispatched_at: datetime
      queued_since: datetime | None = None   # added by draft 08; see the correction below

  class LeaseStore:
      def active(self) -> list[Lease]: ...
      def free_pools(self, all_pools: Sequence[str], drained: Set[str]) -> list[str]: ...
      def acquire(self, lease: Lease) -> None: ...   # raises if coder_pool already leased
      def last_pool_for_repo(self, repo: str) -> str | None: ...   # soft affinity

  def choose_next(queue: Sequence[QueueItem], leases: Sequence[Lease],
                  free_pools: Sequence[str],
                  last_pool_for_repo: Callable[[str], str | None]
                  ) -> tuple[QueueItem, str] | None:
      """Top-ranked item whose repo has no active lease, paired with a free pool."""
  ```

  SQLite table:

  ```sql
  CREATE TABLE IF NOT EXISTS leases (
    coder_pool    TEXT PRIMARY KEY,           -- one row per box; PK enforces one-at-a-time
    repo          TEXT NOT NULL,
    issue_number  INTEGER NOT NULL,
    run_id        TEXT NOT NULL UNIQUE,
    dispatched_at TEXT NOT NULL,              -- ISO-8601 UTC
    queued_since  TEXT                        -- ISO-8601 UTC; issue_cache.first_seen
  );
  ```

  The primary key on `coder_pool` is what makes double-dispatch to one box
  impossible even if the loop is re-entered.

#### Corrected during implementation, 2026-09-19

**The table needs one more column — `queued_since TEXT` — and leaving it out breaks
draft 09.** The issue leaves the `agent` collection the moment the scheduler labels
it `agent-in-progress`, so the 60s inventory poll gets a `200`, `sync_repo` finds the
issue absent from the new list, and its `issue_cache` row — with `first_seen` — is
deleted within a minute of dispatch. Draft 09's Requeue contract ("restore the item
with its **original** `first_seen` so the starvation ceiling is not reset") then has
nothing to restore from, and neither does its acceptance criterion "a requeued item
keeps its original wait time". Decision 5's guarantee — nothing waits more than
`T` — would be quietly false for every item that ever failed.

The lease is the only row that survives the dispatch, so `queued_since` is copied
there from `issue_cache.first_seen`, and `Lease.queued_since` carries it as an
optional trailing field. Nullable on purpose: a lease adopted from an older row, or
written by the manual override for an issue that was never in the cache, has none,
and draft 09 falls back to "now" for that case.

Added by 08 rather than by 09 because it cannot be added later for free:
`CREATE TABLE IF NOT EXISTS` does not add a column to a table that already exists, so
once this draft is deployed the column can only arrive via an explicit `ALTER TABLE`
migration. Nothing else about the drafted schema changed, and the five fields this
draft's `Lease` contract names still read as the contract.

**A lease is also recorded by `POST /api/dispatch-once`**, which the draft left
implicit. It has to be: the manual path is otherwise the one way to put two runs on
a single-slot coder instance, which is the contention the whole service exists to
remove. The endpoint refuses a pool that is already leased with a `409` that names
the holder, and takes the lease on the way out. It deliberately does **not** write
labels — the issue a human names by hand need not be in the `agent` queue at all, and
a rollback would have to decide what to restore.

**One behaviour the draft does not spell out: a failed dispatch can leave an orphan
run.** `RunNotStarted` means fabro created a run and could not start it, so the run
sits in `submitted` forever while the labels are rolled back and no lease is
recorded. The loop reports it with its run id and retries the issue on the next tick,
because the drafted backoff is exactly one tick; nothing cancels the orphan until
draft 09's recovery. Deliberate, and the one residue this draft leaves — which is
also why it must not be left running unattended.

- Verified external contracts:

  Labels are written with the GitHub issues API:

  ```
  POST   /repos/{owner}/{repo}/issues/{n}/labels     {"labels":["agent-in-progress"]}
  DELETE /repos/{owner}/{repo}/issues/{n}/labels/agent
  ```

  `DELETE .../labels/{name}` returns `404` when the label is not present — treat
  that as success, not failure, because a retry must be idempotent.

  Run creation is draft 07's verified `create_run` / `start_run` pair. The four
  live `environment_id` values and their repos are in `repos.toml` (draft 04).

- Behavior rules:
  - Loop every **5s**. Cheap: it only reads local SQLite and dispatches when
    there is a free undrained pool and a non-empty queue.
  - Preconditions: a free undrained pool; a non-empty queue.
  - **Prefer one in-flight run per repo, never idle a box** (decision 6). Take the
    highest-ranked item whose repo has no active lease (diversity). Only when
    every queued repo already has an active lease, fall through and take the
    highest-ranked item that is not itself the one already running (saturation),
    so a single-repo workload fills every box. Exclude the running keys
    explicitly: a label write does not evict the local cache row until the GitHub
    poll, so without it the loop would re-dispatch the issue already on a box.
  - **Soft affinity:** among free pools, prefer `last_pool_for_repo(repo)` when it
    is free. Never wait for it — affinity is a tiebreak only.
  - Dispatch order, and it matters: label **first**, then create, then start, then
    record the lease. If create or start fails, **roll the label back** (remove
    `agent-in-progress`, restore `agent`) and do not record a lease.
  - The label write is the durable receipt draft 09's recovery reads.
- Error and security rules: a GitHub or fabro failure during dispatch must leave
  no lease and no half-applied labels. Log the issue and back off that repo for
  one loop tick rather than retrying instantly in a tight loop.

#### Acceptance run, 2026-09-19

Deployed to the host at 00:33:37 and parked after the run. The loop did exactly what
this draft specifies: both coder instances were free and 29 items were queued, and the
first tick dispatched **one run to each box, one per repo**, in 3 seconds:

```
00:33:37.633  github: jelly-swipe#350 +agent-in-progress
00:33:38.311  github: jelly-swipe#350 -agent
00:33:39.219  fabro: registered workflow version 628845c6e0e1 for fabro-workflows@4d4bb6e3e67e
00:33:39.474  fabro: dispatched jelly-swipe#350 -> run 01M2VHAGXNM9JNEY71085HV82Y status=runnable pool=coders-a
00:33:40.403  github: womens-fantasy-sports#1195 +agent-in-progress
00:33:40.931  github: womens-fantasy-sports#1195 -agent
00:33:42.002  fabro: fabro-workflows@4d4bb6e3e67e unchanged, reusing workflow version 628845c6e0e1
00:33:42.194  fabro: dispatched womens-fantasy-sports#1195 -> run 01M2VHAKMRVQQNTCXZWXMV6CV9 status=runnable pool=coders-b
```

So: the label write precedes the run creation by ~1.3s and precedes the *start* by ~1.8s,
`agent` is gone from both issues and `agent-in-progress` is on both, both leases are
recorded (`coders-a`/jelly-swipe/350 and `coders-b`/womens-fantasy-sports/1195, each with
its `queued_since` from before dispatch), `repo_affinity` has a row per repo, the version
was registered once and reused once, and **only two `dispatch:` lines appear in the log
afterwards** — the loop parked itself exactly as designed. That is every acceptance
criterion this draft can check without waiting hours for a coder stage.

**The one thing it does not check, and cannot yet: the run works a different issue than
the one the scheduler labelled.** `acquire` and `claim` still select (decision 3 deletes
their selection in draft 10), and by the time the run's `acquire` listed `--label agent`
the scheduler had already removed `agent` from its pick. So the run took the *next*
`agent`-labelled issue in that repo:

| | scheduler's pick (labelled + leased) | the run's pick (claimed + worked) |
|---|---|---|
| jelly-swipe | #350 | #351 |
| womens-fantasy-sports | #1195 | #1197 |

The evidence is in the run itself: run `01M2VHAGXNM9JNEY71085HV82Y` carries
`labels: {"source": "scheduler", "issue": "350"}` and its claim comment —
`<!-- fabro:claim:01M2VHAGXNM9JNEY71085HV82Y -->` — is on #351. One dispatch therefore
marks **two** issues `agent-in-progress`: the run's, which is correct, and the
scheduler's, which no run is working and which is invisible to both `acquire` and this
loop's inventory. The operator's repair is this draft's own rollback written backwards —
remove `agent-in-progress`, restore `agent` — and it was applied by hand to #350 and
#1195 after the run. Both were back in the queue within a minute.

This is an interim-state finding, not a defect in the loop: the loop's four steps are
right, and the receipt is the right receipt *for the issue the scheduler picked*. What
is not yet true is the premise underneath it — that the scheduler's pick and the run's
pick are the same issue. That premise arrives with draft 10. Until then, read "the
durable handoff receipt draft 09's recovery reads" as "a receipt on the issue this draft
dispatched, which is not the issue the run is working", and see draft 09's correction
below for why that matters to its recovery pass.


## Acceptance Criteria
- [ ] `uv run pytest` passes.
- [ ] With two free boxes and ≥2 eligible issues in ≥2 repos, two runs start, one
      per box, one per repo.
- [ ] With two free boxes and eligible issues in only **one** repo, exactly
      **one** run starts and the second box stays free.
- [ ] Each dispatched issue gains `agent-in-progress` and loses `agent` **before**
      its run is created.
- [ ] A forced `create_run` failure leaves the issue labelled `agent` with no
      `agent-in-progress` and no lease row.
- [ ] Each run's coder stage runs on the box named by its lease, observed via
      `http://<box>:8000/slots`.
- [ ] The queue page shows both leases with repo, issue and run id.

## Test Expectations
Framework: **pytest**, `uv run pytest` from `ops/scheduler/`. GitHub and fabro
faked with `respx`; SQLite in `tmp_path`.

Concrete case in `tests/test_dispatch.py`:

```python
def test_one_run_per_repo_leaves_the_second_box_free():
    q = [qi("o/a", 1, priority=0), qi("o/a", 2, priority=0)]   # same repo
    leases = [Lease("coders-a", "o/a", 1, "R1", NOW)]
    assert choose_next(q, leases, free_pools=["coders-b"],
                       last_pool_for_repo=lambda r: None) is None

def test_soft_affinity_prefers_the_last_box_but_never_waits():
    q = [qi("o/a", 1, priority=0)]
    # affinity target is busy -> take the free one anyway
    assert choose_next(q, leases=[], free_pools=["coders-b"],
                       last_pool_for_repo=lambda r: "coders-a") == (q[0], "coders-b")
```

## Dependencies
- Blocked by: "Thread `coder_pool` through both root stylesheets";
  "GitHub inventory with ETag caching, and a read-only queue page";
  "Fabro client: register a `.fabro`-rooted version, create and start a run"
- Why blocked: 05 makes `coder_pool` mean something; 06 supplies the ranked queue
  and the store; 07 supplies the dispatcher.
- Blocks: "Release, requeue and recovery"; "Collapse `acquire`/`claim` and add the
  manual-fire script"

## Labels
`feature`, `ops/scheduler`, `priority:high`

## Estimate
Large

## Risk
4 - first unattended dispatch. It writes GitHub labels and starts real runs that
open pull requests. Leases are never released until draft 09, so it must not be
left running unattended after the acceptance run.

## Validator Stopping Point
`uv run pytest` green, two boxes dispatched one-per-repo, labels applied in the
right order, and each run observed on its leased box.
