"""The queue: what is waiting, and in what order.

`rank()` is the whole point of this module. The order it produces is the order
the page shows **and** the order draft 08 dispatches from, so there is exactly one
definition of "next" in the system.

The order, from overview decisions 4 and 5:

1. **Override** — an item the operator bumped to the front (draft 11). Ascending
   rank, so repeated bumps put the newest first. Read from the `overrides` table,
   which is the operator's click and not repo policy: it lives in the scheduler's
   database, is one issue wide, and is cleared when the item is dispatched.
2. **Starvation ceiling** — anything that has waited longer than `T` (default 4h)
   jumps ahead of everything that has not, oldest first. Deliberately a cliff and
   not an aging score: the claim the operator can check by looking is "nothing
   waits more than `T`", and a score cannot be checked by looking.
3. Everyone else, by **repo priority ascending** — a signed integer where
   *smaller is more urgent*, so `-99` beats `0` beats `7` — then by issue number
   ascending.

Note what tier 2 does **not** do: it does not re-sort within itself by priority.
Once an item is past the ceiling, its repo's priority is irrelevant to it, because
the ceiling is the "nothing waits longer than this" guarantee and letting priority
reorder that tier would let a low-priority repo's item wait longer than `T`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import RepoConfig
from .github import Issue
from .store import Store


# Decision 5's `T`. Anything waiting strictly longer than this jumps the front.
DEFAULT_STARVATION_CEILING = timedelta(hours=4)


@dataclass(frozen=True)
class QueueItem:
    """One issue, with the two facts that decide where it sorts."""

    issue: Issue
    repo_priority: int
    override_rank: int | None  # the operator's "next" mark, or None
    waited: timedelta


@dataclass(frozen=True)
class RepoStatus:
    """One schedulable repo's config plus the state of its inventory.

    The page and `GET /api/repos` render `stale` and `pending` from here, which is
    why the two flags are properties rather than template conditionals: an empty
    queue and an unreachable GitHub must not look the same to the operator.
    """

    name: str
    priority: int
    enabled: bool
    item_count: int
    etag: str | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None

    @property
    def stale(self) -> bool:
        """Its most recent fetch failed. It still shows the items it had."""
        return self.last_error is not None

    @property
    def pending(self) -> bool:
        """Never fetched successfully yet — a cold start, not a failure."""
        return self.last_success_at is None and self.last_error is None


def repo_statuses(
    repos: Sequence[RepoConfig], store: Store
) -> list[RepoStatus]:
    """Inventory state for each schedulable repo, in scheduling order."""
    counts = store.issue_counts()
    statuses = []
    for repo in repos:
        state = store.fetch_state(repo.name)
        statuses.append(
            RepoStatus(
                name=repo.name,
                priority=repo.priority,
                enabled=repo.enabled,
                item_count=counts.get(repo.name, 0),
                etag=state.etag if state else None,
                last_attempt_at=state.last_attempt_at if state else None,
                last_success_at=state.last_success_at if state else None,
                last_error=state.last_error if state else None,
            )
        )
    return statuses


def rank(items: Sequence[QueueItem], ceiling: timedelta) -> list[QueueItem]:
    """Return `items` in dispatch order. Does not mutate its argument."""
    overridden = sorted(
        (item for item in items if item.override_rank is not None),
        key=lambda item: (item.override_rank, item.issue.number),
    )
    starved = sorted(
        (
            item
            for item in items
            if item.override_rank is None and item.waited > ceiling
        ),
        key=lambda item: (item.issue.first_seen, item.issue.number),
    )
    waiting = sorted(
        (
            item
            for item in items
            if item.override_rank is None and item.waited <= ceiling
        ),
        key=lambda item: (item.repo_priority, item.issue.number),
    )
    return overridden + starved + waiting


def build_queue(
    repos: Sequence[RepoConfig],
    store: Store,
    now: datetime,
    ceiling: timedelta = DEFAULT_STARVATION_CEILING,
) -> list[QueueItem]:
    """The ranked queue over `repos`, from whatever the store currently holds.

    `repos` must already be the schedulable set (`enabled = false` rows excluded —
    `SchedulerConfig.schedulable_repos()`), because this is what draft 08 will
    dispatch from. Any cached item whose repo is not in `repos` is dropped: an
    issue in a repo that is no longer scheduled for, or that has been disabled,
    must not appear as work waiting.
    """
    priorities = {repo.name: repo.priority for repo in repos}
    # One SELECT for every override, keyed by `(repo, number)`. A cached issue with
    # no row is simply not overridden. Deliberately **not** filtered by repo here:
    # an override for an issue that has since left the cache is unreachable anyway,
    # because only cached issues become items.
    overrides = store.override_ranks()

    items = []
    for issue in store.issues():
        priority = priorities.get(issue.repo)
        if priority is None:
            continue
        items.append(
            QueueItem(
                issue=issue,
                repo_priority=priority,
                override_rank=overrides.get((issue.repo, issue.number)),
                waited=now - issue.first_seen,
            )
        )

    return rank(items, ceiling)
