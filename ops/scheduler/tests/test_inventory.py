"""The poll loop's rules, against a faked GitHub and real `tmp_path` SQLite.

The two behaviours worth pinning here are the ones that are wrong *quietly* if they
regress: a `304` emptying the cache (the page then says "starvation" because GitHub
had a good minute), and one repo's failure killing the loop for all four (the page
then freezes while `/health` keeps answering `ok`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from fabro_scheduler.config import RepoConfig
from fabro_scheduler.github import Issue
from fabro_scheduler.inventory import InventoryPoller
from fabro_scheduler.queue import build_queue, repo_statuses
from fabro_scheduler.store import Store

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
ISSUES_URL = "https://api.github.com/repos/o/a/issues"
REPO = RepoConfig("o/a", 0, "python")


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def poller(store: Store) -> InventoryPoller:
    return InventoryPoller([REPO], store, "ghp_test", interval_seconds=60.0)


def issue(number: int = 1, first_seen: datetime = NOW) -> Issue:
    return Issue("o/a", number, f"issue {number}", frozenset({"agent"}), first_seen)


@respx.mock
def test_a_304_keeps_the_cached_items_and_their_first_seen(store):
    # The draft's concrete case, at the caller level: `fetch_issues` returning an
    # empty list must not become an empty cache.
    store.sync_repo("o/a", [issue(1, NOW - timedelta(hours=3))], '"e1"', NOW)
    respx.get(ISSUES_URL).mock(return_value=httpx.Response(304))

    poller(store).poll_repo(REPO)

    cached = store.issues()
    assert [i.number for i in cached] == [1]
    assert cached[0].first_seen == NOW - timedelta(hours=3)
    state = store.fetch_state("o/a")
    assert state is not None and state.last_error is None


@respx.mock
def test_a_200_replaces_the_cache_and_a_vanished_issue_leaves_it(store):
    store.sync_repo("o/a", [issue(1), issue(2)], '"e1"', NOW)
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[{"number": 1, "title": "issue 1", "state": "open",
                   "labels": [{"name": "agent"}]}],
            headers={"etag": '"e2"'},
        )
    )

    poller(store).poll_repo(REPO)

    assert [i.number for i in store.issues()] == [1]
    state = store.fetch_state("o/a")
    assert state is not None and state.etag == '"e2"'


@respx.mock
def test_a_failed_fetch_keeps_the_cached_items_and_flags_the_repo(store):
    store.sync_repo("o/a", [issue(1)], '"e1"', NOW)
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )

    poller(store).poll_repo(REPO)

    assert [i.number for i in store.issues()] == [1]
    [status] = repo_statuses([REPO], store)
    assert status.stale is True
    assert "500" in (status.last_error or "")
    # And the item is still a queue item: a stale repo is not an empty repo.
    assert [i.issue.number for i in build_queue([REPO], store, NOW, timedelta(hours=4))] == [1]


@respx.mock
def test_an_unexpected_error_is_recorded_and_does_not_kill_the_poller(store):
    # Not a `GitHubError`: a bug in the parse, a malformed payload, a full disk.
    # It must be visible on the page as stale, not a thread that stopped.
    respx.get(ISSUES_URL).mock(side_effect=RuntimeError("kaboom"))
    p = poller(store)

    p.poll_repo(REPO)  # must not raise

    [status] = repo_statuses([REPO], store)
    assert status.stale is True
    assert "kaboom" in (status.last_error or "")


@respx.mock
def test_the_loop_survives_one_repo_failing_and_still_polls_the_next(store):
    other = RepoConfig("o/b", 1, "ts")
    respx.get(ISSUES_URL).mock(side_effect=RuntimeError("kaboom"))
    respx.get("https://api.github.com/repos/o/b/issues").mock(
        return_value=httpx.Response(
            200,
            json=[{"number": 7, "title": "t", "state": "open",
                   "labels": [{"name": "agent"}]}],
            headers={"etag": '"e2"'},
        )
    )

    p = InventoryPoller([REPO, other], store, "ghp_test")
    p.refresh_all()

    assert [i.repo for i in store.issues()] == ["o/b"]
    assert repo_statuses([REPO], store)[0].stale is True


@respx.mock
def test_a_missing_token_is_recorded_per_repo_rather_than_raising(store):
    p = InventoryPoller([REPO], store, "")
    p.refresh_all()

    [status] = repo_statuses([REPO], store)
    assert status.stale is True
    assert "GITHUB_TOKEN" in (status.last_error or "")
