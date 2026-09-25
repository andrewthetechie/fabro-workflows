"""Ranking, and the store the ranking reads.

`rank()` decides both what the page shows and what draft 08 dispatches, so the
ordering assertions here are the specification rather than a description of it.
Everything runs against `tmp_path` SQLite; there is no network and no container.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fabro_scheduler.config import RepoConfig, load_config
from fabro_scheduler.github import Issue
from fabro_scheduler.queue import QueueItem, build_queue, rank, repo_statuses
from fabro_scheduler.store import Store

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
CEILING = timedelta(hours=4)


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def issue(
    repo: str = "o/a",
    number: int = 1,
    *,
    labels: tuple[str, ...] = ("agent",),
    first_seen: datetime = NOW,
) -> Issue:
    return Issue(repo, number, f"issue {number}", frozenset(labels), first_seen)


def item(
    repo: str = "o/a",
    number: int = 1,
    *,
    priority: int = 0,
    waited: timedelta = timedelta(0),
    first_seen: datetime | None = None,
    override: int | None = None,
) -> QueueItem:
    seen = first_seen if first_seen is not None else NOW - waited
    return QueueItem(
        issue=issue(repo, number, first_seen=seen),
        repo_priority=priority,
        override_rank=override,
        waited=waited,
    )


def repo(name: str, priority: int, enabled: bool = True) -> RepoConfig:
    return RepoConfig(name=name, priority=priority, environment_id="python", enabled=enabled)


# --- ranking: the starvation ceiling ------------------------------------------------


def test_ceiling_beats_priority():
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    urgent_repo = QueueItem(
        issue=Issue("o/a", 1, "t", frozenset({"agent"}), now - timedelta(minutes=5)),
        repo_priority=-99,
        override_rank=None,
        waited=timedelta(minutes=5),
    )
    old_low = QueueItem(
        issue=Issue("o/b", 2, "t", frozenset({"agent"}), now - timedelta(hours=5)),
        repo_priority=7,
        override_rank=None,
        waited=timedelta(hours=5),
    )
    assert [i.issue.number for i in rank([urgent_repo, old_low], ceiling=CEILING)] == [2, 1]


def test_two_starved_items_go_oldest_first_not_by_priority():
    # Within the starved tier repo priority is irrelevant, on purpose: the
    # ceiling is "nothing waits longer than T", and letting priority reorder this
    # tier would let a priority-7 repo's item wait longer than T.
    ancient_low = item("o/b", 2, priority=7, waited=timedelta(hours=9))
    recent_high = item("o/a", 1, priority=-99, waited=timedelta(hours=5))
    assert [i.issue.number for i in rank([recent_high, ancient_low], CEILING)] == [2, 1]


def test_waited_exactly_at_the_ceiling_is_not_starved():
    # `>` and not `>=`: the claim is "nothing waits *longer* than T".
    at_ceiling = item("o/c", 3, priority=7, waited=CEILING)
    waiting = item("o/a", 1, priority=0, waited=timedelta(hours=1))
    assert [i.issue.number for i in rank([at_ceiling, waiting], CEILING)] == [1, 3]


# --- ranking: priority and issue number ---------------------------------------------


def test_priority_is_signed_and_smallest_wins():
    items = [
        item("o/late", 1, priority=7),
        item("o/urgent", 2, priority=-99),
        item("o/zero", 3, priority=0),
    ]
    assert [i.issue.number for i in rank(items, CEILING)] == [2, 3, 1]


def test_issue_number_breaks_a_repo_priority_tie():
    items = [
        item("o/a", 9, priority=1),
        item("o/b", 2, priority=1),
        item("o/c", 5, priority=1),
    ]
    assert [i.issue.number for i in rank(items, CEILING)] == [2, 5, 9]


def test_two_issues_in_one_repo_never_reorder_each_other_by_wait():
    # Same repo, same priority: the number decides, so the queue is deterministic
    # rather than dependent on the order rows came back from SQLite.
    newer = item("o/a", 8, priority=1, waited=timedelta(0))
    older = item("o/a", 3, priority=1, waited=timedelta(hours=1))
    assert [i.issue.number for i in rank([newer, older], CEILING)] == [3, 8]


# --- ranking: the override tier (draft 11 fills the field) --------------------------


def test_an_override_outranks_both_the_ceiling_and_repo_priority():
    bumped = item("o/b", 2, priority=7, waited=timedelta(minutes=1), override=-1)
    starved = item("o/c", 3, priority=-99, waited=timedelta(hours=9))
    waiting = item("o/a", 1, priority=-99, waited=timedelta(minutes=1))
    assert [i.issue.number for i in rank([starved, waiting, bumped], CEILING)] == [2, 3, 1]


def test_repeated_bumps_put_the_newest_rank_first():
    first = item("o/a", 1, override=0)
    second = item("o/b", 2, override=-1)
    assert [i.issue.number for i in rank([first, second], CEILING)] == [2, 1]


def test_rank_does_not_mutate_its_argument():
    items = [item("o/a", 1, priority=7), item("o/b", 2, priority=-99)]
    original = list(items)
    rank(items, CEILING)
    assert items == original


def test_rank_of_nothing_is_nothing():
    assert rank([], CEILING) == []


# --- the store: first_seen is never reset -------------------------------------------


def test_upsert_issue_preserves_first_seen_on_a_second_sighting(store):
    original = NOW - timedelta(hours=5)
    store.upsert_issue(issue("o/a", 7, first_seen=original))
    store.upsert_issue(issue("o/a", 7, first_seen=NOW))

    stored = store.get_issue("o/a", 7)
    assert stored is not None
    assert stored.first_seen == original  # a refresh must not reset the wait
    assert stored.title == "issue 7"


def test_sync_repo_keeps_first_seen_for_survivors_and_drops_the_departed(store):
    original = NOW - timedelta(hours=3)
    store.sync_repo("o/a", [issue("o/a", 1, first_seen=original), issue("o/a", 2)], '"e1"', NOW)

    later = NOW + timedelta(minutes=1)
    # 1 survives, 2 is gone (closed / relabelled / turned into a PR).
    store.sync_repo("o/a", [issue("o/a", 1, first_seen=later)], '"e2"', later)

    assert [i.number for i in store.issues()] == [1]
    survivor = store.get_issue("o/a", 1)
    assert survivor is not None
    assert survivor.first_seen == original


def test_sync_repo_with_nothing_empties_that_repo(store):
    store.sync_repo("o/a", [issue("o/a", 1)], '"e1"', NOW)
    store.sync_repo("o/a", [], '"e2"', NOW)
    assert store.issues() == []
    assert store.issue_counts() == {}


def test_sync_repo_leaves_other_repos_alone(store):
    store.sync_repo("o/a", [issue("o/a", 1)], '"e1"', NOW)
    store.sync_repo("o/b", [issue("o/b", 2)], '"e2"', NOW)
    store.sync_repo("o/a", [], '"e3"', NOW)
    assert [i.repo for i in store.issues()] == ["o/b"]


# --- the store: failed fetches keep the cache ---------------------------------------


def test_a_failed_fetch_keeps_items_the_etag_and_marks_the_repo_stale(store):
    store.sync_repo("o/a", [issue("o/a", 1)], '"e1"', NOW)
    store.note_failure("o/a", NOW + timedelta(minutes=1), "o/a: GitHub returned 403")

    state = store.fetch_state("o/a")
    assert state is not None
    assert state.last_error == "o/a: GitHub returned 403"
    assert state.etag == '"e1"'  # the next attempt is conditional again
    assert state.last_success_at == NOW
    # The cached items survive: an unreachable GitHub is not an empty queue.
    assert [i.number for i in store.issues()] == [1]


def test_a_later_success_clears_the_stale_flag(store):
    store.note_failure("o/a", NOW, "boom")
    store.sync_repo("o/a", [issue("o/a", 1)], '"e2"', NOW + timedelta(minutes=1))

    state = store.fetch_state("o/a")
    assert state is not None
    assert state.last_error is None
    assert state.last_success_at == NOW + timedelta(minutes=1)


def test_a_304_is_a_success_and_keeps_everything_else(store):
    store.sync_repo("o/a", [issue("o/a", 1, first_seen=NOW - timedelta(hours=5))], '"e1"', NOW)
    later = NOW + timedelta(minutes=1)
    store.note_not_modified("o/a", '"e1"', later)

    state = store.fetch_state("o/a")
    assert state is not None
    assert state.etag == '"e1"'
    assert state.last_success_at == later
    stored = store.get_issue("o/a", 1)
    assert stored is not None
    assert stored.first_seen == NOW - timedelta(hours=5)


def test_a_304_after_a_failure_clears_the_error(store):
    store.note_failure("o/a", NOW, "boom")
    store.note_not_modified("o/a", '"e1"', NOW + timedelta(minutes=1))
    state = store.fetch_state("o/a")
    assert state is not None
    assert state.etag == '"e1"'


def test_fetch_state_is_none_for_a_repo_never_touched(store):
    assert store.fetch_state("o/never") is None


# --- build_queue --------------------------------------------------------------------


def test_build_queue_computes_waited_from_first_seen(store):
    store.upsert_issue(issue("o/a", 1, first_seen=NOW - timedelta(hours=2)))
    [queued] = build_queue([repo("o/a", 0)], store, NOW, CEILING)
    assert queued.waited == timedelta(hours=2)
    assert queued.repo_priority == 0
    assert queued.override_rank is None


def test_build_queue_takes_priority_from_the_config(store):
    store.upsert_issue(issue("o/a", 1))
    store.upsert_issue(issue("o/b", 2))
    items = build_queue([repo("o/a", 5), repo("o/b", -3)], store, NOW, CEILING)
    assert [(i.issue.repo, i.repo_priority) for i in items] == [("o/b", -3), ("o/a", 5)]


def test_build_queue_drops_repos_that_are_not_schedulable(store):
    store.upsert_issue(issue("o/a", 1))
    store.upsert_issue(issue("o/disabled", 2))
    store.upsert_issue(issue("o/unknown", 3))
    items = build_queue([repo("o/a", 0)], store, NOW, CEILING)
    assert [i.issue.repo for i in items] == ["o/a"]


def test_build_queue_is_empty_when_a_repo_is_cached_but_disabled(tmp_path, store):
    # `build_queue` takes the already-filtered schedulable list, so the guarantee
    # is `schedulable_repos()`'s, and this is where the two meet.
    path = tmp_path / "repos.toml"
    path.write_text(
        '[[repo]]\nname = "o/a"\npriority = 0\nenvironment_id = "python"\n'
        "enabled = false\n"
    )
    cfg = load_config(path, env={})
    store.upsert_issue(issue("o/a", 1))

    assert cfg.schedulable_repos() == []
    assert build_queue(cfg.schedulable_repos(), store, NOW, CEILING) == []


def test_build_queue_starves_an_issue_the_cache_has_held_past_the_ceiling(store):
    store.upsert_issue(issue("o/a", 1, first_seen=NOW - timedelta(hours=5)))
    store.upsert_issue(issue("o/b", 2, first_seen=NOW - timedelta(minutes=1)))
    items = build_queue([repo("o/a", 7), repo("o/b", -99)], store, NOW, CEILING)
    assert [i.issue.number for i in items] == [1, 2]


# --- repo_statuses ------------------------------------------------------------------


def test_repo_statuses_walk_pending_then_ok_then_stale(store):
    config = [repo("o/a", 0)]

    [pending] = repo_statuses(config, store)
    assert pending.pending is True
    assert pending.stale is False
    assert pending.item_count == 0

    store.sync_repo("o/a", [issue("o/a", 1)], '"e1"', NOW)
    [ok] = repo_statuses(config, store)
    assert (ok.pending, ok.stale, ok.item_count, ok.etag) == (False, False, 1, '"e1"')

    store.note_failure("o/a", NOW, "boom")
    [stale] = repo_statuses(config, store)
    assert (stale.pending, stale.stale, stale.item_count) == (False, True, 1)
    assert stale.last_error == "boom"


# --- ranking: the `priority` label tier (ADR 0011) ------------------------------------


def labelled(
    repo: str,
    number: int,
    labels: tuple[str, ...],
    *,
    priority: int = 0,
    waited: timedelta = timedelta(0),
    override: int | None = None,
) -> QueueItem:
    return QueueItem(
        issue=issue(repo, number, labels=labels, first_seen=NOW - waited),
        repo_priority=priority,
        override_rank=override,
        waited=waited,
    )


def test_the_priority_label_outranks_the_ceiling_and_repo_priority():
    flagged = labelled("o/b", 5, ("agent", "priority"), priority=7, waited=timedelta(minutes=1))
    starved = item("o/c", 3, priority=-99, waited=timedelta(hours=9))
    waiting = item("o/a", 1, priority=-99, waited=timedelta(minutes=1))
    assert [i.issue.number for i in rank([starved, waiting, flagged], CEILING)] == [5, 3, 1]


def test_an_override_outranks_the_priority_label():
    flagged = labelled("o/a", 1, ("agent", "priority"), waited=timedelta(hours=1))
    bumped = item("o/b", 2, override=-1)
    assert [i.issue.number for i in rank([flagged, bumped], CEILING)] == [2, 1]


def test_two_priority_items_go_oldest_first_not_by_repo_priority_or_number():
    older = labelled("o/a", 9, ("agent", "priority"), priority=50, waited=timedelta(hours=2))
    newer = labelled("o/b", 1, ("agent", "priority"), priority=-99, waited=timedelta(hours=1))
    assert [i.issue.number for i in rank([newer, older], CEILING)] == [9, 1]


def test_an_overridden_priority_item_appears_once():
    both = labelled("o/a", 1, ("agent", "priority"), override=-1)
    assert [i.issue.number for i in rank([both], CEILING)] == [1]


def test_build_queue_reads_the_priority_label_from_the_cache(store):
    store.upsert_issue(issue("o/a", 1, first_seen=NOW - timedelta(minutes=5)))
    store.upsert_issue(issue("o/b", 2, labels=("agent", "priority"), first_seen=NOW))
    items = build_queue([repo("o/a", -99), repo("o/b", 50)], store, NOW, CEILING)
    assert [i.issue.number for i in items] == [2, 1]
