"""The coder lease: one whole run's claim on one coder instance.

A **Coder instance** is a llama.cpp server with exactly one slot, so whoever holds
it blocks every other run for a whole turn. A **Coder lease** is the scheduler's
claim on one of them for one **Queue item**, recorded as
`(box, repo, issue, run_id, dispatched_at)` and held from dispatch until the run
reaches a terminal state (CONTEXT.md; overview decision 1 and ADR 0006).

It is held for the **whole run**, not a stage at a time. That is the design's
central simplifying choice: a run that blocks on a human gate keeps its box, and
the cost of that is accepted on purpose, because releasing on `blocked` would mean
re-acquiring a box to continue and there is no mechanism for that.

**The exclusion is enforced by the database, not by this class.** `leases` has
`coder_pool` as its primary key, and `Store.acquire_lease` resolves the conflict
inside the inserting statement. So `LeaseStore` can be constructed twice, or two
scheduler processes can share the file, and one box still cannot be handed out
twice. `acquire` raises `LeaseConflict` after the fact; it does not defend the
invariant itself.

**Nothing here releases a lease.** That is draft 09, and until it lands a lease is
held until the row is deleted by hand. The consequence is bounded and worth
stating: with two coder instances the loop can hold at most two leases, so once
both boxes are taken it stops dispatching on its own. It does not spin, and it
does not leak boxes beyond the two that exist.

**A lease carries the item's wait, and that is deliberate.** `queued_since` is the
one field here that draft 08's contract does not name. The issue leaves the `agent`
collection the moment the scheduler labels it `agent-in-progress`, so the
inventory poll deletes its `issue_cache` row within a minute of dispatch and
takes `first_seen` with it. A requeued item would then come back with a fresh wait,
which restarts the starvation ceiling and makes decision 5's "nothing waits more
than `T`" quietly false for every item that ever failed. Draft 09's `requeue` is
the consumer; it falls back to "now" when the field is absent (a lease adopted from
an older row, or one written by the manual override for an issue not in the cache).
Adding the column now is what makes it possible at all: `CREATE TABLE IF NOT EXISTS`
will not add a column to a table that already exists, and this one exists on the
host as soon as draft 08 is deployed.
"""

from __future__ import annotations

from collections.abc import Sequence, Set
from dataclasses import dataclass
from datetime import datetime

from .store import Store


@dataclass(frozen=True)
class Lease:
    """One coder instance, held for one queue item's run."""

    coder_pool: str  # "coders-a" | "coders-b"
    repo: str
    issue_number: int
    run_id: str
    dispatched_at: datetime
    # When the item entered the queue, copied from `issue_cache.first_seen` at
    # dispatch. Optional and last so the five fields draft 08 names still read as
    # the contract; see the module docstring for what it is for and what it costs
    # to leave out.
    queued_since: datetime | None = None


class LeaseConflict(RuntimeError):
    """That coder instance is already leased, so nothing was changed.

    Carries the lease that holds it when there was one to name, which is what lets
    an operator answer "who has this box?" from the error message alone.
    """

    def __init__(self, message: str, *, held_by: Lease | None = None) -> None:
        super().__init__(message)
        self.held_by = held_by


class LeaseStore:
    """The lease table, in the vocabulary the dispatch loop thinks in.

    A thin layer over `Store` on purpose: all the SQL, and the single connection
    and lock it is only safe on, stay in one module, and this is where the two
    policy answers that are not SQL live — what counts as a *free* pool, and which
    pool a repo prefers.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    def active(self) -> list[Lease]:
        """Every lease currently held, ordered by coder instance.

        There is no expiry and no filtering by repo: a lease is live until the row
        is gone, which for now means until an operator deletes it.
        """
        return [
            Lease(
                coder_pool=row["coder_pool"],
                repo=row["repo"],
                issue_number=row["issue_number"],
                run_id=row["run_id"],
                dispatched_at=datetime.fromisoformat(row["dispatched_at"]),
                queued_since=(
                    None
                    if row["queued_since"] is None
                    else datetime.fromisoformat(row["queued_since"])
                ),
            )
            for row in self._store.lease_rows()
        ]

    def free_pools(self, all_pools: Sequence[str], drained: Set[str]) -> list[str]:
        """The coder instances a new run may be dispatched to, in `all_pools` order.

        Free means "no lease and not drained". **Drain** never destroys work: a
        drained pool keeps running whatever it already holds and only stops
        *new* dispatch (CONTEXT.md). Nothing sets `drained` in this draft — draft
        11 owns the `pool_state` table and the controls — but the exclusion is
        here so that adding it is a lookup, not a change to the selection rule.
        """
        busy = {lease.coder_pool for lease in self.active()}
        return [
            pool for pool in all_pools if pool not in busy and pool not in drained
        ]

    def acquire(self, lease: Lease) -> None:
        """Record `lease`, or raise `LeaseConflict` having changed nothing.

        The one place affinity is written, because this is the one place a lease
        starts. Recording it on release instead would lose it whenever a run is
        released by a path that has no box to name.
        """
        stored = self._store.acquire_lease(
            coder_pool=lease.coder_pool,
            repo=lease.repo,
            issue_number=lease.issue_number,
            run_id=lease.run_id,
            dispatched_at=lease.dispatched_at,
            queued_since=lease.queued_since,
        )
        if stored:
            return

        held_by = next(
            (held for held in self.active() if held.coder_pool == lease.coder_pool),
            None,
        )
        if held_by is None:
            # The insert was refused but the pool reads as free: the only other
            # uniqueness rule on the table is `run_id`, so that is what it was.
            raise LeaseConflict(
                f"run {lease.run_id} already holds a coder lease"
            )
        raise LeaseConflict(
            f"{lease.coder_pool} is already leased by "
            f"{held_by.repo}#{held_by.issue_number} (run {held_by.run_id})",
            held_by=held_by,
        )

    def last_pool_for_repo(self, repo: str) -> str | None:
        """Where this repo last ran, or `None` if it never has. Soft affinity."""
        return self._store.last_pool_for_repo(repo)
