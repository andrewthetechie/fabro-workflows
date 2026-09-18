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
    dispatched_at TEXT NOT NULL               -- ISO-8601 UTC
  );
  ```

  The primary key on `coder_pool` is what makes double-dispatch to one box
  impossible even if the loop is re-entered.

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
  - Loop every **5s**. Cheap: it only reads local SQLite and dispatches when all
    three preconditions hold.
  - Preconditions, all required: a free undrained pool; a non-empty queue; the
    candidate's repo has no active lease.
  - **One in-flight run per repo.** If the top-ranked item's repo is already
    leased, skip to the next-ranked item from a different repo. Do **not** idle
    the box.
  - **Soft affinity:** among free pools, prefer `last_pool_for_repo(repo)` when it
    is free. Never wait for it — affinity is a tiebreak only.
  - Dispatch order, and it matters: label **first**, then create, then start, then
    record the lease. If create or start fails, **roll the label back** (remove
    `agent-in-progress`, restore `agent`) and do not record a lease.
  - The label write is the durable receipt draft 09's recovery reads.
- Error and security rules: a GitHub or fabro failure during dispatch must leave
  no lease and no half-applied labels. Log the issue and back off that repo for
  one loop tick rather than retrying instantly in a tight loop.

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
