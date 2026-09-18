"""`/health` over `TestClient` — no server, no container, no network.

The payload is a contract: later drafts and the host's healthcheck both read it,
so its shape and its ordering are asserted rather than assumed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fabro_scheduler.app import build_app, create_app
from fabro_scheduler.config import load_config

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app(load_config(TRACKED_REPOS, env={})))


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
    body = TestClient(build_app(load_config(path, env={}))).get("/health").json()
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
    # /health is unauthenticated. The container holds FABRO_API_TOKEN and
    # GITHUB_TOKEN for drafts 06 and 07; none of that may appear in a response.
    path = tmp_path / "repos.toml"
    path.write_text(
        '[[repo]]\nname = "o/a"\npriority = 0\nenvironment_id = "python"\n'
    )
    app = create_app(
        path,
        env={
            "FABRO_API_TOKEN": "tok-do-not-echo",
            "GITHUB_TOKEN": "ghp-do-not-echo",
            "SESSION_SECRET": "secret-do-not-echo",
            "FABRO_API_URL": "http://user:pw@fabro.example/api/v1",
        },
    )
    body = TestClient(app).get("/health").text
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
