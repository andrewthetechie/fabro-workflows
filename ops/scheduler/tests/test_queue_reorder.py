"""Queue reordering: up / down / to-top buttons and drag-and-drop (feature).

The three controls and the drag reorder all persist an explicit queue order (the
same `overrides` table the old "Next"/bump wrote), which `rank()` turns into the
ranked prefix. These assert the *ordering* half: that the controls move an item
to where the operator asked, that the persisted order matches what the page shows,
and that the writes are one transaction so a concurrent dispatch-clear cannot
interleave. Dispatch behaviour (clearing the mark when the item goes) is covered
by the existing bump tests, because a ranked row clears the same way.

Repos are the same synthetic two as `test_overrides.py`: `o/urgent` at priority
-99 (ranked first on its own) and `o/normal` at 0, so an "up" that reaches the
front has to beat a better-priority repo to prove it is the operator's order that
wins, not the auto-sort.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from fabro_scheduler.app import build_app
from fabro_scheduler.config import RepoConfig, load_config
from fabro_scheduler.github import Issue
from fabro_scheduler.queue import build_queue
from fabro_scheduler.store import Store

REPOS = """
[[repo]]
name = "o/urgent"
priority = -99
environment_id = "python"

[[repo]]
name = "o/normal"
priority = 0
environment_id = "python"
"""

CEILING = timedelta(hours=4)


def _client(tmp_path) -> tuple[TestClient, Store]:
    path = tmp_path / "repos.toml"
    path.write_text(REPOS)
    config = load_config(path, env={})
    store = Store(tmp_path / "scheduler.db")
    return TestClient(build_app(config, store, github_token="ghp_test")), store


def _issue(repo: str, number: int) -> Issue:
    return Issue(
        repo, number, f"issue {number}", frozenset({"agent"}), datetime.now(UTC)
    )


def _seed(store: Store):
    # o/urgent would rank first on its own; o/normal#2 then o/normal#3 behind it.
    store.upsert_issue(_issue("o/urgent", 1))
    store.upsert_issue(_issue("o/normal", 2))
    store.upsert_issue(_issue("o/normal", 3))


def _order(store: Store) -> list[int]:
    items = build_queue(
        [RepoConfig("o/urgent", -99, "python"), RepoConfig("o/normal", 0, "python")],
        store,
        datetime.now(UTC),
        CEILING,
    )
    return [(i.issue.number, i.override_rank) for i in items]


# --- to-top ----------------------------------------------------------------------


def test_to_top_moves_an_item_to_the_very_front(tmp_path):
    client, store = _client(tmp_path)
    _seed(store)

    r = client.post("/api/queue/o/normal/2/top")

    assert r.status_code == 200
    assert r.json()["queue"][0] == {"repo": "o/normal", "issue_number": 2}
    # The persisted order (via build_queue) has o/normal#2 first, beating the
    # better-priority repo, because it is now ranked ahead of it.
    assert [i for i, _ in _order(store)] == [2, 1, 3]


def test_to_top_is_idempotent_when_already_first(tmp_path):
    client, store = _client(tmp_path)
    _seed(store)
    client.post("/api/queue/o/normal/3/top")
    before = [i for i, _ in _order(store)]

    client.post("/api/queue/o/normal/3/top")

    assert [i for i, _ in _order(store)] == before


# --- up / down -------------------------------------------------------------------


def test_up_swaps_an_item_with_its_immediate_neighbour(tmp_path):
    client, store = _client(tmp_path)
    _seed(store)
    client.post("/api/queue/o/normal/2/top")  # [2, 1, 3]

    r = client.post("/api/queue/o/normal/3/up")

    assert r.status_code == 200
    assert [i for i, _ in _order(store)] == [2, 3, 1]


def test_down_swaps_an_item_with_its_immediate_neighbour(tmp_path):
    client, store = _client(tmp_path)
    _seed(store)
    client.post("/api/queue/o/normal/2/top")  # [2, 1, 3]

    client.post("/api/queue/o/normal/2/down")

    assert [i for i, _ in _order(store)] == [1, 2, 3]


def test_up_at_the_front_is_a_noop(tmp_path):
    client, store = _client(tmp_path)
    _seed(store)
    client.post("/api/queue/o/normal/2/top")
    before = [i for i, _ in _order(store)]

    client.post("/api/queue/o/normal/2/up")

    assert [i for i, _ in _order(store)] == before


def test_down_at_the_back_is_a_noop(tmp_path):
    client, store = _client(tmp_path)
    _seed(store)
    client.post("/api/queue/o/normal/2/top")
    client.post("/api/queue/o/normal/3/down")  # now last
    before = [i for i, _ in _order(store)]

    client.post("/api/queue/o/normal/3/down")

    assert [i for i, _ in _order(store)] == before


# --- drag-and-drop reorder -------------------------------------------------------


def test_reorder_persists_the_submitted_order_exactly(tmp_path):
    client, store = _client(tmp_path)
    _seed(store)

    body = {
        "items": [
            {"repo": "o/normal", "issue_number": 3},
            {"repo": "o/urgent", "issue_number": 1},
            {"repo": "o/normal", "issue_number": 2},
        ]
    }
    r = client.post("/api/queue/reorder", json=body)

    assert r.status_code == 200
    assert [i for i, _ in _order(store)] == [3, 1, 2]


def test_reorder_refuses_a_duplicate_item(tmp_path):
    client, store = _client(tmp_path)
    _seed(store)

    r = client.post(
        "/api/queue/reorder",
        json={
            "items": [
                {"repo": "o/normal", "issue_number": 2},
                {"repo": "o/normal", "issue_number": 2},
            ]
        },
    )

    assert r.status_code == 400
    assert "duplicate" in r.json()["detail"]


def test_reorder_refuses_an_unknown_item_without_writing_anything(tmp_path):
    client, store = _client(tmp_path)
    _seed(store)

    r = client.post(
        "/api/queue/reorder",
        json={
            "items": [
                {"repo": "o/normal", "issue_number": 2},
                {"repo": "o/normal", "issue_number": 99},
            ]
        },
    )

    assert r.status_code == 404
    # Nothing was written: a partial order with the good item ranked would leave
    # the unknown one silently dropped from the operator's view.
    assert all(rank is None for _, rank in _order(store))


# --- the shared reorder controls refuse bad refs, like bump ----------------------


def test_reorder_controls_refuse_a_repo_that_is_not_schedulable(tmp_path):
    client, _ = _client(tmp_path)
    assert client.post("/api/queue/o%2Fnope/2/up").status_code == 404
    assert client.post("/api/queue/o%2Fnope/2/top").status_code == 404


def test_reorder_controls_refuse_an_issue_that_is_not_queued(tmp_path):
    client, _ = _client(tmp_path)
    assert client.post("/api/queue/o%2Fnormal/99/down").status_code == 404
    assert "not in the queue" in client.post(
        "/api/queue/o%2Fnormal/99/top"
    ).json()["detail"]


def test_reorder_controls_are_post_only(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/queue/o/normal/2/up").status_code == 405
    assert client.get("/api/queue/o/normal/2/down").status_code == 405
    assert client.get("/api/queue/o/normal/2/top").status_code == 405
    assert client.get("/api/queue/reorder").status_code == 405


# --- persistence -----------------------------------------------------------------


def test_a_reorder_survives_a_reopen(tmp_path):
    client, store = _client(tmp_path)
    _seed(store)
    body = {
        "items": [
            {"repo": "o/normal", "issue_number": 3},
            {"repo": "o/urgent", "issue_number": 1},
            {"repo": "o/normal", "issue_number": 2},
        ]
    }
    client.post("/api/queue/reorder", json=body)
    store.close()

    reopened = Store(tmp_path / "scheduler.db")
    try:
        assert [i for i, _ in _order(reopened)] == [3, 1, 2]
    finally:
        reopened.close()
