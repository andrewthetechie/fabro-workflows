"""The HTTP surface over `TestClient` — no server, no container, no live network.

`/health`'s payload is a contract: later drafts and the host's healthcheck both
read it, so its shape and its ordering are asserted rather than assumed. The page
and `/api/queue` are asserted on the one thing that matters about them — that the
order they show is the order `rank()` produced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from fabro_scheduler.app import build_app, create_app
from fabro_scheduler.config import load_config
from fabro_scheduler.github import Issue
from fabro_scheduler.inventory import InventoryPoller
from fabro_scheduler.store import Store

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
def config():
    return load_config(TRACKED_REPOS, env={})


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


@pytest.fixture
def client(config, store) -> TestClient:
    return TestClient(build_app(config, store, github_token="ghp_test"))


def issue(repo: str, number: int, *, waited: timedelta = timedelta(0), labels=("agent",)) -> Issue:
    # Relative to the *real* clock, not to `NOW`: the app computes each item's
    # wait from `datetime.now`, so a fixed `first_seen` in the past would put every
    # row past the starvation ceiling and the priority assertions below would be
    # measuring the wrong tier.
    return Issue(
        repo, number, f"issue {number}", frozenset(labels), datetime.now(UTC) - waited
    )


# --- /health -----------------------------------------------------------------------


def test_health_is_ok_and_lists_the_four_repos_in_scheduling_order(client):
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert [r["name"] for r in body["repos"]] == [
        "andrewthetechie/jelly-swipe",
        "andrewthetechie/lawncare-saas",
        "andrewthetechie/womens-fantasy-sports",
        "andrewthetechie/writers-app",
    ]


def test_health_repo_rows_carry_the_documented_keys(client):
    row = client.get("/health").json()["repos"][0]
    assert row == {
        "name": "andrewthetechie/jelly-swipe",
        "priority": 0,
        "environment_id": "python",
        "enabled": True,
    }


def test_health_lists_the_two_per_box_pools_and_not_the_load_balanced_one(client):
    # `coders` is the fallback for unpinned runs; dispatching through it would
    # reintroduce the contention the scheduler exists to remove.
    assert client.get("/health").json()["coder_pools"] == ["coders-a", "coders-b"]


def test_health_reports_a_disabled_row_with_its_flag(tmp_path):
    path = tmp_path / "repos.toml"
    path.write_text(
        '[[repo]]\nname = "o/on"\npriority = 0\nenvironment_id = "python"\n'
        '[[repo]]\nname = "o/off"\npriority = 1\nenvironment_id = "ts"\n'
        "enabled = false\n"
    )
    opened = Store(tmp_path / "scheduler.db")
    try:
        cfg = load_config(path, env={})
        body = TestClient(build_app(cfg, opened)).get("/health").json()
    finally:
        opened.close()
    assert body["repos"] == [
        {
            "name": "o/on",
            "priority": 0,
            "environment_id": "python",
            "enabled": True,
        },
        {
            "name": "o/off",
            "priority": 1,
            "environment_id": "ts",
            "enabled": False,
        },
    ]


def test_health_never_echoes_the_environment(tmp_path):
    # /health is unauthenticated. The container holds GITHUB_TOKEN and, from
    # draft 07, FABRO_API_TOKEN; none of that may appear in a response.
    path = tmp_path / "repos.toml"
    path.write_text(
        '[[repo]]\nname = "o/a"\npriority = 0\nenvironment_id = "python"\n'
    )
    opened = Store(tmp_path / "scheduler.db")
    try:
        app = create_app(
            path,
            env={
                "FABRO_API_TOKEN": "tok-do-not-echo",
                "GITHUB_TOKEN": "ghp-do-not-echo",
                "SESSION_SECRET": "secret-do-not-echo",
                "FABRO_API_URL": "http://user:pw@fabro.example/api/v1",
            },
            store=opened,
        )
        body = TestClient(app).get("/health").text
    finally:
        opened.close()
    for leaked in (
        "tok-do-not-echo",
        "ghp-do-not-echo",
        "secret-do-not-echo",
        "user:pw",
    ):
        assert leaked not in body


def test_health_does_not_publish_the_fabro_api_url(client):
    # A URL can carry a credential in it; it is not needed by any consumer of
    # /health and there is no auth in front of it.
    assert "fabro_api_url" not in client.get("/health").text
    assert "32276" not in client.get("/health").text


def test_health_reports_whether_a_github_token_is_configured(config, store):
    # A boolean, never the value: this is the signal draft 12's monitor reads, and
    # a service that cannot read GitHub looks healthy from the outside otherwise.
    without = TestClient(build_app(config, store)).get("/health").json()
    with_token = TestClient(build_app(config, store, github_token="ghp_x")).get("/health").json()

    assert without["github"] == {"configured": False, "poll_seconds": 60}
    assert with_token["github"] == {"configured": True, "poll_seconds": 60}


def test_health_reports_the_starvation_ceiling(config, store):
    body = TestClient(build_app(config, store)).get("/health").json()
    assert body["starvation_ceiling_seconds"] == 4 * 60 * 60


# --- the queue page and its JSON ---------------------------------------------------


def test_api_queue_is_a_ranked_list(config, store, client):
    store.upsert_issue(issue("andrewthetechie/writers-app", 5))  # priority 2
    store.upsert_issue(issue("andrewthetechie/jelly-swipe", 9))  # priority 0
    store.upsert_issue(issue("andrewthetechie/jelly-swipe", 2))

    body = client.get("/api/queue").json()

    assert [(row["repo"], row["number"]) for row in body] == [
        ("andrewthetechie/jelly-swipe", 2),
        ("andrewthetechie/jelly-swipe", 9),
        ("andrewthetechie/writers-app", 5),
    ]
    assert body[0]["repo_priority"] == 0
    assert body[0]["override_rank"] is None
    assert body[0]["labels"] == ["agent"]
    assert "waited_seconds" in body[0]


def test_the_page_renders_the_ranked_queue(config, store, client):
    store.upsert_issue(issue("andrewthetechie/writers-app", 5))  # priority 2
    store.upsert_issue(issue("andrewthetechie/jelly-swipe", 9))  # priority 0

    html = client.get("/").text

    assert html.index("jelly-swipe") < html.index("writers-app")
    assert "#9" in html
    assert "issue 5" in html
    assert "Starvation ceiling" in html


def test_the_page_says_starvation_when_there_is_no_work(client):
    html = client.get("/").text
    assert "Starvation" in html
    assert "no queue item in any schedulable repo" in html


def test_a_starved_item_sorts_above_a_better_priority_one(config, store, client):
    store.upsert_issue(issue("andrewthetechie/jelly-swipe", 1))  # priority 0, fresh
    store.upsert_issue(issue("andrewthetechie/writers-app", 2, waited=timedelta(hours=5)))

    body = client.get("/api/queue").json()
    assert [row["number"] for row in body] == [2, 1]


def test_the_page_marks_a_stale_repo_and_still_shows_its_items(config, store, client):
    store.sync_repo(
        "andrewthetechie/jelly-swipe", [issue("andrewthetechie/jelly-swipe", 4)], '"e1"', NOW
    )
    store.note_failure("andrewthetechie/jelly-swipe", NOW, "o/a: GitHub returned 403")

    html = client.get("/").text

    assert "issue 4" in html  # not silently emptied
    assert "stale" in html
    assert "GitHub returned 403" in html


def test_api_repos_reports_freshness_etag_and_counts(config, store, client):
    store.sync_repo(
        "andrewthetechie/jelly-swipe", [issue("andrewthetechie/jelly-swipe", 4)], '"e1"', NOW
    )

    rows = {row["repo"]: row for row in client.get("/api/repos").json()}

    assert rows["andrewthetechie/jelly-swipe"]["items"] == 1
    assert rows["andrewthetechie/jelly-swipe"]["etag"] == '"e1"'
    assert rows["andrewthetechie/jelly-swipe"]["stale"] is False
    assert rows["andrewthetechie/jelly-swipe"]["pending"] is False
    assert rows["andrewthetechie/writers-app"]["pending"] is True


def test_a_disabled_repo_never_reaches_the_queue_or_the_repo_table(tmp_path):
    path = tmp_path / "repos.toml"
    path.write_text(
        '[[repo]]\nname = "o/on"\npriority = 0\nenvironment_id = "python"\n'
        '[[repo]]\nname = "o/off"\npriority = 1\nenvironment_id = "ts"\n'
        "enabled = false\n"
    )
    opened = Store(tmp_path / "scheduler.db")
    try:
        opened.upsert_issue(issue("o/off", 1))
        cfg = load_config(path, env={})
        http = TestClient(build_app(cfg, opened))
        assert http.get("/api/queue").json() == []
        assert [row["repo"] for row in http.get("/api/repos").json()] == ["o/on"]
    finally:
        opened.close()


def test_the_page_never_renders_the_token(tmp_path):
    path = tmp_path / "repos.toml"
    path.write_text(
        '[[repo]]\nname = "o/a"\npriority = 0\nenvironment_id = "python"\n'
    )
    opened = Store(tmp_path / "scheduler.db")
    try:
        app = create_app(
            path, env={"GITHUB_TOKEN": "ghp-do-not-echo"}, store=opened
        )
        http = TestClient(app)
        for route in ("/", "/health", "/api/queue", "/api/repos"):
            assert "ghp-do-not-echo" not in http.get(route).text
    finally:
        opened.close()


# --- the inventory, end to end through the app -------------------------------------


@respx.mock
def test_only_eligible_issues_reach_the_page(config, store, client):
    """The acceptance rule, through the whole stack: GitHub JSON → cache → page.

    A pull request carrying `agent`, and an issue already `agent-in-progress`, must
    not appear anywhere — while the one real queue item does.
    """
    respx.get("https://api.github.com/repos/andrewthetechie/jelly-swipe/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 1,
                    "title": "a real issue",
                    "state": "open",
                    "labels": [{"name": "agent"}],
                },
                {
                    "number": 2,
                    "title": "a pull request",
                    "state": "open",
                    "labels": [{"name": "agent"}],
                    "pull_request": {"url": "https://api.github.com/..."},
                },
                {
                    "number": 3,
                    "title": "already running",
                    "state": "open",
                    "labels": [{"name": "agent"}, {"name": "agent-in-progress"}],
                },
            ],
            headers={"etag": '"e1"'},
        )
    )

    for slug in ("lawncare-saas", "womens-fantasy-sports", "writers-app"):
        # The other three repos are polled too, and each needs an answer: respx
        # fails loudly on an unmocked request, which is the behaviour worth
        # having. Empty is the honest answer here — this test is about the filter.
        respx.get(f"https://api.github.com/repos/andrewthetechie/{slug}/issues").mock(
            return_value=httpx.Response(200, json=[], headers={"etag": '"x"'})
        )

    poller = InventoryPoller(config.schedulable_repos(), store, "ghp_test")
    poller.refresh_all()

    body = client.get("/api/queue").json()
    assert [row["number"] for row in body] == [1]
    html = client.get("/").text
    assert "a real issue" in html
    assert "a pull request" not in html
    assert "already running" not in html
