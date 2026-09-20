# Add `archive_and_release_lease`: the atomic release seam

## Tracer-Bullet Outcome
`Store` can now delete a lease and insert its `run_history` row in **one transaction**.
Call it with a `RunOutcome` and the lease is gone, the row is there, and there is no
instant in between where a crash loses both. Nothing calls it yet.

## User Story
As the operator, I want the run's record written in the same transaction that frees the
box, so that a scheduler crash at the wrong microsecond cannot destroy the only durable
trace of a four-hour run.

## Description
Add one method to `Store` beside the existing `release_lease`, and one method to
`LeaseStore` beside the existing `release`. The old pair stays: the orphan path still
releases without archiving, and task 06 guards that.

The existing `release_lease` already reads-then-deletes inside one transaction precisely
so the lease's facts cannot be lost between the two steps. The history row inherits that
exposure — a process that dies between the delete and a separate insert loses the row
*and* the lease, leaving nothing to reconstruct it from. So this is not a new call beside
the release; it is the same transaction doing one more statement.

## Context Pack
- Source decisions: ADR 0008 decision 6 — the insert shares the release transaction.
- Repo facts: `Store` holds **one** connection under **one** lock, and every statement —
  reads included — runs inside `with self._lock` (and `with self._conn` when it writes).
  `store.py` returns `sqlite3.Row`, never a domain type, because `lease.py` imports
  `store.py` and the reverse would be circular; the row→`Lease` mapping lives in
  `lease.py:168-187`.
- Non-goals: no `reconcile.py` change, no caller. The three terminal release sites are
  task 04, the lost-run path is task 05.

## Delivery Strategy
- Shape: Prefactor
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/store.py     (add archive_and_release_lease)
  ops/scheduler/src/fabro_scheduler/lease.py     (add archive_and_release)
  ops/scheduler/tests/test_store_history.py      (extend)
  ```

- Interfaces and names:

  The **existing** method to place the new one beside, copied verbatim from
  `store.py:521-540`. Note the read-before-delete and the single `with`:

  ```python
  def release_lease(self, run_id: str) -> sqlite3.Row | None:
      """Drop the lease for `run_id`, returning the row it held (or `None`). ..."""
      with self._lock, self._conn:
          row = self._conn.execute(
              "SELECT coder_pool, repo, issue_number, run_id, dispatched_at, "
              "queued_since FROM leases WHERE run_id = ?",
              (run_id,),
          ).fetchone()
          if row is None:
              return None
          self._conn.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
          return row
  ```

  Add to `Store`, immediately after it:

  ```python
  def archive_and_release_lease(
      self, run_id: str, outcome: RunOutcome, *, at: datetime | None = None
  ) -> sqlite3.Row | None:
      """Record the run in `run_history` and drop its lease, in one transaction.

      The archiving counterpart to `release_lease`, and the only way a release
      should be taken once a run's ending is known. Returns the lease row exactly
      as `release_lease` does -- the requeue rule reads `queued_since` off it --
      or `None` when `run_id` is not leased, in which case nothing is written.

      One transaction is the whole point. The row and the lease are the same fact
      seen from two sides, and a crash between a delete and a separate insert
      would lose both.

      `at` is the release time, defaulting to now. It is what `finished_at`
      becomes when the outcome does not carry one, which is every path that has no
      run projection to read `timestamps.completed_at` from.
      """
      released = _stamp(at)
      with self._lock, self._conn:
          row = self._conn.execute(
              "SELECT coder_pool, repo, issue_number, run_id, dispatched_at, "
              "queued_since FROM leases WHERE run_id = ?",
              (run_id,),
          ).fetchone()
          if row is None:
              return None
          self._conn.execute(
              "INSERT INTO run_history ("
              "  run_id, coder_pool, repo, issue_number, dispatched_at, finished_at,"
              "  kind, reason, category, requeue_attempt, pr_lookup, pr_number,"
              "  pr_url, merged"
              ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
              "ON CONFLICT(run_id) DO NOTHING",
              (
                  row["run_id"],
                  row["coder_pool"],
                  row["repo"],
                  row["issue_number"],
                  row["dispatched_at"],
                  (
                      released
                      if outcome.finished_at is None
                      else outcome.finished_at.isoformat()
                  ),
                  outcome.kind,
                  outcome.reason,
                  outcome.category,
                  outcome.requeue_attempt,
                  outcome.pr_lookup,
                  outcome.pr_number,
                  outcome.pr_url,
                  outcome.merged,
              ),
          )
          self._conn.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
          return row
  ```

  `_stamp` already exists in `store.py:715-717` and is the module's timestamp helper:

  ```python
  def _stamp(value: datetime | None) -> str:
      """An ISO-8601 UTC timestamp, defaulting to now."""
      return (datetime.now(UTC) if value is None else value).isoformat()
  ```

  The **existing** `LeaseStore` method to place the new one beside, copied verbatim from
  `lease.py:103-113`:

  ```python
  def release(self, run_id: str) -> Lease | None:
      """Drop the lease for `run_id`, returning it (or `None` if it was absent). ..."""
      row = self._store.release_lease(run_id)
      return None if row is None else _lease_from_row(row)
  ```

  Add to `LeaseStore`, immediately after it:

  ```python
  def archive_and_release(self, run_id: str, outcome: RunOutcome) -> Lease | None:
      """Record the run in `run_history`, then drop its lease. Returns the lease.

      The archiving form of `release`, and what every path that KNOWS how the run
      ended should call. `release` stays for the one path that does not: the
      orphan requeue, which has a synthetic lease with an empty `run_id` and no
      run behind it at all.
      """
      row = self._store.archive_and_release_lease(run_id, outcome)
      return None if row is None else _lease_from_row(row)
  ```

  `lease.py` currently imports `from .store import Store`. Widen it to
  `from .store import RunOutcome, Store`.

- Verified external contracts: None. No HTTP surface.

- Behavior rules:
  - `run_id` not leased → return `None` and write **nothing**. Do not insert an
    orphan history row for a lease that is not there.
  - `ON CONFLICT(run_id) DO NOTHING` — a re-release of the same run (a reconcile pass
    that runs twice over a row a concurrent pass already took) must not raise. The
    first write wins.
  - `finished_at` is `outcome.finished_at.isoformat()` when present, otherwise the
    release time `at`.
  - `outcome.merged` is passed straight through as a `bool` or `None`; `sqlite3`
    adapts it to `1`/`0`/`NULL`.
  - Do not change `release_lease`. Both methods exist from here on.

- Error and security rules: None. No credential and no log line — the caller logs, as
  `reconcile.py` already does for every release.

## Acceptance Criteria
- [ ] `archive_and_release_lease` on a leased `run_id` returns the lease row, deletes the
      lease, and leaves exactly one `run_history` row with the lease's `coder_pool`,
      `repo`, `issue_number` and `dispatched_at`.
- [ ] `archive_and_release_lease` on an unleased `run_id` returns `None` and writes no row.
- [ ] `finished_at` is the outcome's when given, and the release time when not.
- [ ] Calling it twice for the same `run_id` does not raise and leaves one row.
- [ ] `LeaseStore.archive_and_release` returns a `Lease` with `queued_since` preserved.
- [ ] `release_lease` and `LeaseStore.release` still behave exactly as before, writing no
      `run_history` row.

## Test Expectations
Framework: **pytest 8**. Command: `cd ops/scheduler && uv run pytest tests/test_store_history.py`.
Extend `ops/scheduler/tests/test_store_history.py` from task 01.

```python
from datetime import UTC, datetime

from fabro_scheduler.lease import Lease, LeaseStore
from fabro_scheduler.store import RunOutcome, Store

DISPATCHED = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
FINISHED = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
FF = "andrewthetechie/jelly-swipe"


def _acquire(store: Store, run_id: str = "R1") -> None:
    store.acquire_lease(
        coder_pool="coders-a", repo=FF, issue_number=350,
        run_id=run_id, dispatched_at=DISPATCHED,
        queued_since=datetime(2026, 9, 20, 9, 0, tzinfo=UTC),
    )


def _history(store: Store) -> list:
    return store._conn.execute("SELECT * FROM run_history").fetchall()


def test_archive_writes_the_row_and_drops_the_lease(store):
    _acquire(store)
    row = store.archive_and_release_lease(
        "R1", RunOutcome(kind="succeeded", reason="completed", finished_at=FINISHED)
    )
    assert row["coder_pool"] == "coders-a"
    assert store.lease_rows() == []
    (kept,) = _history(store)
    assert kept["run_id"] == "R1"
    assert kept["repo"] == FF
    assert kept["issue_number"] == 350
    assert kept["coder_pool"] == "coders-a"
    assert kept["dispatched_at"] == DISPATCHED.isoformat()
    assert kept["finished_at"] == FINISHED.isoformat()
    assert kept["kind"] == "succeeded"
    assert kept["reason"] == "completed"
    assert kept["pr_lookup"] == "none"
    assert kept["merged"] is None


def test_archive_without_a_lease_writes_nothing(store):
    assert store.archive_and_release_lease("nope", RunOutcome(kind="succeeded")) is None
    assert _history(store) == []


def test_finished_at_falls_back_to_the_release_time(store):
    _acquire(store)
    store.archive_and_release_lease("R1", RunOutcome(kind="lost"), at=FINISHED)
    (kept,) = _history(store)
    assert kept["finished_at"] == FINISHED.isoformat()


def test_archiving_twice_is_idempotent(store):
    _acquire(store)
    store.archive_and_release_lease("R1", RunOutcome(kind="succeeded"))
    assert store.archive_and_release_lease("R1", RunOutcome(kind="failed")) is None
    assert len(_history(store)) == 1


def test_lease_store_archive_preserves_queued_since(store):
    _acquire(store)
    leased = LeaseStore(store).archive_and_release("R1", RunOutcome(kind="succeeded"))
    assert isinstance(leased, Lease)
    assert leased.queued_since == datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


def test_plain_release_still_writes_no_history(store):
    _acquire(store)
    assert store.release_lease("R1") is not None
    assert _history(store) == []
```

`Store.acquire_lease`'s real signature, for the helper above (`store.py:609-618`):

```python
def acquire_lease(
    self, *, coder_pool: str, repo: str, issue_number: int, run_id: str,
    dispatched_at: datetime, queued_since: datetime | None = None,
) -> bool: ...
```

## Dependencies
- Blocked by: `Add the run_history table and the RunOutcome contract`
- Why blocked: supplies the `run_history` table this method inserts into and the
  `RunOutcome` type it takes.
- Blocks: `Write a row on every terminal release`

## Labels
`feature`, `scheduler`, `priority:high`

## Estimate
Small

## Risk
2 - One new method beside an existing one, with no caller. The existing release path is
untouched, so nothing in production changes.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full. The repository is valid: the new
methods have no callers and `release_lease` is unchanged.
