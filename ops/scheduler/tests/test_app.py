"""The HTTP surface over `TestClient` — no server, no container, no live network.

`/health`'s payload is a contract: later drafts and the host's healthcheck both
read it, so its shape and its ordering are asserted rather than assumed. The page
and `/api/queue` are asserted on the one thing that matters about them — that the
order they show is the order `rank()` produced. `POST /api/dispatch-once` is
asserted on the one thing that matters about it — that it creates and starts
exactly one run with the repo's configured `environment_id` — and on every way it
refuses before creating anything.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from fabro_scheduler.app import build_app, create_app
from fabro_scheduler.config import load_config
from fabro_scheduler.fabro import FabroClient
from fabro_scheduler.github import Issue
from fabro_scheduler.inventory import InventoryPoller
from fabro_scheduler.lease import Lease, LeaseStore
from fabro_scheduler.store import Store
from fabro_scheduler.workflow_version import WorkflowVersionError

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

FABRO_API = "http://10.10.0.32:32276/api/v1"
FABRO_TOKEN = "dev-token-do-not-echo"
VERSION_ID = "f" * 64


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


def test_health_reports_whether_the_dispatch_path_is_armed(config, store):
    # Same rule as the GitHub token: a boolean, never the value. A client injected
    # by a test counts as armed — it is a client.
    unarmed = TestClient(build_app(config, store)).get("/health").json()
    armed = TestClient(build_app(config, store, fabro_token=FABRO_TOKEN)).get(
        "/health"
    ).json()

    assert unarmed["fabro"] == {"configured": False}
    assert armed["fabro"] == {"configured": True}
    assert FABRO_TOKEN not in json.dumps(armed)


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


# --- POST /api/dispatch-once -------------------------------------------------------


def _fabro_client(fake_checkout) -> FabroClient:
    return FabroClient(FABRO_API, FABRO_TOKEN, clone=fake_checkout.clone)


def _dispatch_client(config, store, fake_checkout) -> TestClient:
    return TestClient(
        build_app(
            config,
            store,
            github_token="ghp_test",
            fabro_client=_fabro_client(fake_checkout),
        )
    )


def _mock_the_three_calls(run_id: str = "R1", kind: str = "runnable"):
    register = respx.post(f"{FABRO_API}/workflow-versions").mock(
        return_value=httpx.Response(201, json={"workflow_version_id": VERSION_ID})
    )
    create = respx.post(f"{FABRO_API}/runs").mock(
        return_value=httpx.Response(
            201, json={"id": run_id, "lifecycle": {"status": {"kind": "submitted"}}}
        )
    )
    respx.post(f"{FABRO_API}/runs/{run_id}/start").mock(
        return_value=httpx.Response(
            200, json={"id": run_id, "lifecycle": {"status": {"kind": kind}}}
        )
    )
    return register, create


@respx.mock
def test_dispatch_once_creates_and_starts_one_run_for_the_named_issue(
    config, store, fake_checkout
):
    register, create = _mock_the_three_calls()

    response = _dispatch_client(config, store, fake_checkout).post(
        "/api/dispatch-once",
        json={
            "repo": "andrewthetechie/jelly-swipe",
            "issue_number": 123,
            "coder_pool": "coders-a",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "R1",
        "workflow_version_id": VERSION_ID,
        "commit_sha": fake_checkout.sha,
        "version_reused": False,
        "status": "runnable",
    }

    # `environment_id` comes from the repo's row in `repos.toml`, never from the
    # caller: `jelly-swipe` is `python`, and `python` is the image that can build it.
    sent = json.loads(create.calls.last.request.content)
    assert sent["environment_id"] == "python"
    assert sent["args"]["inputs"] == {"issue_number": 123, "coder_pool": "coders-a"}
    assert sent["target"]["repo"] == "andrewthetechie/jelly-swipe"
    # The tree is the whole `.fabro`, entrypoint included: a package-rooted version
    # cannot resolve `../_shared/review-merge/review-merge.fabro`.
    payload = json.loads(register.calls.last.request.content)
    assert payload["entrypoint"] == "workflows/backlog/workflow.fabro"
    assert "workflows/_shared/review-merge/review-merge.fabro" in payload["files"]


@respx.mock
def test_dispatch_once_twice_reuses_the_version_and_creates_two_runs(
    config, store, fake_checkout
):
    register = respx.post(f"{FABRO_API}/workflow-versions").mock(
        return_value=httpx.Response(201, json={"workflow_version_id": VERSION_ID})
    )
    ids = iter(["R1", "R2"])
    create = respx.post(f"{FABRO_API}/runs").mock(
        side_effect=lambda request: httpx.Response(
            201, json={"id": next(ids), "lifecycle": {"status": {"kind": "submitted"}}}
        )
    )
    respx.post(url__regex=rf"{FABRO_API}/runs/R[12]/start").mock(
        return_value=httpx.Response(
            200, json={"lifecycle": {"status": {"kind": "runnable"}}}
        )
    )
    http = _dispatch_client(config, store, fake_checkout)

    first = http.post(
        "/api/dispatch-once",
        json={
            "repo": "andrewthetechie/jelly-swipe",
            "issue_number": 123,
            "coder_pool": "coders-a",
        },
    ).json()
    # The second fire names the other box on purpose: a manual dispatch takes the
    # lease, so firing twice at one pool is a 409 rather than two runs sharing a
    # single-slot instance (`test_dispatch_once_refuses_a_leased_pool`).
    second = http.post(
        "/api/dispatch-once",
        json={
            "repo": "andrewthetechie/jelly-swipe",
            "issue_number": 123,
            "coder_pool": "coders-b",
        },
    ).json()

    assert register.call_count == 1
    assert create.call_count == 2
    assert (first["run_id"], second["run_id"]) == ("R1", "R2")
    assert first["version_reused"] is False
    assert second["version_reused"] is True
    assert {lease.coder_pool for lease in LeaseStore(store).active()} == {
        "coders-a",
        "coders-b",
    }


@respx.mock
def test_dispatch_once_refuses_a_leased_pool(config, store, fake_checkout):
    # The manual path must respect the lease or it is the one way back to two runs
    # on one single-slot coder instance — the contention this service exists to
    # remove. The refusal happens before the create call, so nothing is started.
    register, create = _mock_the_three_calls()
    http = _dispatch_client(config, store, fake_checkout)
    body = {
        "repo": "andrewthetechie/jelly-swipe",
        "issue_number": 123,
        "coder_pool": "coders-a",
    }
    assert http.post("/api/dispatch-once", json=body).status_code == 200

    response = http.post("/api/dispatch-once", json=body)

    assert response.status_code == 409
    assert "coders-a" in response.json()["detail"]
    assert "R1" in response.json()["detail"]
    assert create.call_count == 1  # nothing was created for the refused call
    assert len(LeaseStore(store).active()) == 1


@respx.mock
def test_dispatch_once_records_a_lease_for_the_manual_run(config, store, fake_checkout):
    _mock_the_three_calls()
    queued = issue("andrewthetechie/jelly-swipe", 123)
    store.upsert_issue(queued)

    _dispatch_client(config, store, fake_checkout).post(
        "/api/dispatch-once",
        json={
            "repo": "andrewthetechie/jelly-swipe",
            "issue_number": 123,
            "coder_pool": "coders-a",
        },
    )

    lease = LeaseStore(store).active()[0]
    assert lease.coder_pool == "coders-a"
    assert lease.repo == "andrewthetechie/jelly-swipe"
    assert lease.issue_number == 123
    assert lease.run_id == "R1"
    assert lease.dispatched_at.tzinfo is not None
    # The wait travels with the lease so draft 09's requeue can restore it — the
    # manual path writes no labels, but the poll drops the row all the same once
    # the run's own `claim` relabels the issue.
    assert lease.queued_since == queued.first_seen


def test_the_page_shows_the_active_leases(config, store, client):
    LeaseStore(store).acquire(
        Lease(
            coder_pool="coders-b",
            repo="andrewthetechie/writers-app",
            issue_number=42,
            run_id="01MRUN",
            dispatched_at=datetime.now(UTC),
        )
    )

    html = client.get("/").text

    assert "coders-b" in html
    assert "andrewthetechie/writers-app" in html
    assert "#42" in html
    assert "01MRUN" in html
    assert "leased" in html
    # Both configured pools are always named, so "free" and "not configured"
    # cannot be read as the same state.
    assert "coders-a" in html
    assert "accepting" in html


def test_the_page_offers_a_bump_control_per_queue_item(config, store, client):
    store.upsert_issue(issue("andrewthetechie/writers-app", 5))

    html = client.get("/").text

    # The control posts to the route that exists, with the repo urlencoded so a
    # name can never break out of the action attribute.
    assert "/api/queue/andrewthetechie/writers-app/5/bump" in html
    assert ">Next<" in html


def test_the_page_offers_drain_and_cancel_for_each_pool(config, store, client):
    LeaseStore(store).acquire(
        Lease(
            coder_pool="coders-a",
            repo="andrewthetechie/writers-app",
            issue_number=42,
            run_id="01MRUN",
            dispatched_at=datetime.now(UTC),
        )
    )

    html = client.get("/").text

    assert "/api/pools/coders-a/drain" in html
    assert "/api/pools/coders-b/drain" in html
    assert "/api/pools/coders-a/cancel" in html
    assert "/api/pools/coders-b/cancel" in html
    # The cancel button is disabled on the pool with no lease, so an operator
    # cannot aim it at nothing and get a 409 for their trouble.
    assert "disabled" in html


def test_the_page_shows_an_undrain_control_for_a_drained_box(config, store, client):
    store.set_drained("coders-a", True)

    html = client.get("/").text

    assert "/api/pools/coders-a/undrain" in html
    assert "/api/pools/coders-a/drain" not in html
    assert "/api/pools/coders-b/drain" in html
    assert "drained" in html


def test_the_page_says_no_lease_is_held_when_none_is(config, store, client):
    assert "No coder instance is leased." in client.get("/").text


def test_the_page_says_when_automatic_dispatch_is_off(config, store):
    # `build_app` without `start_dispatch_loop` is what a test and a manually run
    # process get. The operator must be able to tell that page from the armed one.
    html = TestClient(build_app(config, store, github_token="ghp_test")).get("/").text
    assert "Automatic dispatch is off" in html


def test_the_page_stays_silent_about_dispatch_when_it_is_armed(config, store, fake_checkout):
    app = build_app(
        config,
        store,
        github_token="ghp_test",
        fabro_client=_fabro_client(fake_checkout),
        start_dispatch_loop=True,
    )
    # `start_dispatch_loop` only arms the loop; the thread starts with the ASGI
    # lifespan, which a bare `TestClient` here does not enter. That is why the
    # inference is from the app's configuration, not from a thread count.
    html = TestClient(app).get("/").text
    assert "Automatic dispatch is off" not in html


def test_dispatch_once_is_503_when_the_token_is_missing(config, store):
    # A scheduler that cannot create runs must say so in one line, not fail the
    # container's healthcheck in a restart loop that names nothing.
    response = TestClient(build_app(config, store, github_token="ghp_test")).post(
        "/api/dispatch-once",
        json={
            "repo": "andrewthetechie/jelly-swipe",
            "issue_number": 1,
            "coder_pool": "coders-a",
        },
    )

    assert response.status_code == 503
    assert "FABRO_API_TOKEN" in response.json()["detail"]


def test_dispatch_once_refuses_a_repo_that_is_not_in_repos_toml(config, store):
    response = TestClient(build_app(config, store, fabro_token=FABRO_TOKEN)).post(
        "/api/dispatch-once",
        json={"repo": "o/nope", "issue_number": 1, "coder_pool": "coders-a"},
    )

    assert response.status_code == 404
    assert "o/nope" in response.json()["detail"]


def test_dispatch_once_refuses_a_disabled_repo(tmp_path):
    path = tmp_path / "repos.toml"
    path.write_text(
        '[[repo]]\nname = "o/off"\npriority = 0\nenvironment_id = "python"\n'
        "enabled = false\n"
    )
    opened = Store(tmp_path / "scheduler.db")
    try:
        response = TestClient(
            build_app(load_config(path, env={}), opened, fabro_token=FABRO_TOKEN)
        ).post(
            "/api/dispatch-once",
            json={"repo": "o/off", "issue_number": 1, "coder_pool": "coders-a"},
        )
    finally:
        opened.close()

    assert response.status_code == 409
    assert "disabled" in response.json()["detail"]


def test_dispatch_once_refuses_the_load_balanced_pool(config, store):
    # `coders` spans both boxes — dispatching through it would put the run back on
    # whichever box LiteLLM picked, which is the contention the scheduler exists
    # to remove.
    for pool in ("coders", "", "coders-c"):
        response = TestClient(build_app(config, store, fabro_token=FABRO_TOKEN)).post(
            "/api/dispatch-once",
            json={
                "repo": "andrewthetechie/jelly-swipe",
                "issue_number": 1,
                "coder_pool": pool,
            },
        )
        assert response.status_code == 422, pool
        assert "coders-a, coders-b" in response.json()["detail"]


def test_dispatch_once_refuses_a_malformed_body(config, store):
    http = TestClient(build_app(config, store, fabro_token=FABRO_TOKEN))

    # An unknown key is refused rather than ignored: a misspelled one would
    # dispatch something other than what was typed.
    extra = http.post(
        "/api/dispatch-once",
        json={
            "repo": "andrewthetechie/jelly-swipe",
            "issue_number": 1,
            "coder_pool": "coders-a",
            "environment_id": "rust-node",
        },
    )
    assert extra.status_code == 422

    for issue_number in (0, -3, "not a number", True, "7", 7.5):
        bad = http.post(
            "/api/dispatch-once",
            json={
                "repo": "andrewthetechie/jelly-swipe",
                "issue_number": issue_number,
                "coder_pool": "coders-a",
            },
        )
        assert bad.status_code == 422, issue_number


@respx.mock
def test_dispatch_once_answers_502_when_the_workflow_tree_cannot_be_fetched(
    config, store
):
    # A failure before fabro is called at all — git missing, clone timing out. No
    # run exists to cancel, but the operator gets fabro's own sentence rather than a
    # traceback.
    class NoTree:
        def clone(self, repo: str, ref: str):
            raise WorkflowVersionError(
                "git is not on PATH; the workflow tree cannot be fetched"
            )

    response = TestClient(
        build_app(
            config,
            store,
            github_token="ghp_test",
            fabro_client=FabroClient(FABRO_API, FABRO_TOKEN, clone=NoTree().clone),
        )
    ).post(
        "/api/dispatch-once",
        json={
            "repo": "andrewthetechie/jelly-swipe",
            "issue_number": 1,
            "coder_pool": "coders-a",
        },
    )

    assert response.status_code == 502
    assert "workflow tree" in response.json()["detail"]
    assert LeaseStore(store).active() == []


@respx.mock
def test_dispatch_once_answers_502_with_fabros_own_detail(config, store, fake_checkout):
    respx.post(f"{FABRO_API}/workflow-versions").mock(
        return_value=httpx.Response(201, json={"workflow_version_id": VERSION_ID})
    )
    respx.post(f"{FABRO_API}/runs").mock(
        return_value=httpx.Response(
            422, json={"errors": [{"detail": "unknown environment id `nope`"}]}
        )
    )

    response = _dispatch_client(config, store, fake_checkout).post(
        "/api/dispatch-once",
        json={
            "repo": "andrewthetechie/jelly-swipe",
            "issue_number": 1,
            "coder_pool": "coders-a",
        },
    )

    assert response.status_code == 502
    assert "unknown environment id" in response.json()["detail"]


@respx.mock
def test_dispatch_once_never_echoes_the_token(config, store, fake_checkout):
    respx.post(f"{FABRO_API}/workflow-versions").mock(
        return_value=httpx.Response(
            422, json={"errors": [{"detail": "not valid UTF-8"}]}
        )
    )

    response = _dispatch_client(config, store, fake_checkout).post(
        "/api/dispatch-once",
        json={
            "repo": "andrewthetechie/jelly-swipe",
            "issue_number": 1,
            "coder_pool": "coders-a",
        },
    )

    assert response.status_code == 502
    assert FABRO_TOKEN not in response.text


from datetime import UTC, datetime

from fabro_scheduler.store import RunOutcome

DISPATCHED = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
FF = "andrewthetechie/jelly-swipe"


def _archive(store, run_id: str, *, finished: str, number: int = 350, **outcome) -> None:
    store.acquire_lease(
        coder_pool="coders-a", repo=FF, issue_number=number,
        run_id=run_id, dispatched_at=DISPATCHED,
    )
    store.archive_and_release_lease(
        run_id,
        RunOutcome(
            kind=outcome.pop("kind", "succeeded"),
            finished_at=datetime.fromisoformat(finished),
            **outcome,
        ),
    )


def test_history_is_empty_before_anything_is_released(client):
    response = client.get("/api/history")
    assert response.status_code == 200
    assert response.json() == []


def test_history_is_newest_finished_first(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00")

    body = client.get("/api/history").json()

    assert [row["run_id"] for row in body] == ["B", "A"]
    assert body[0]["repo"] == FF
    assert body[0]["coder_pool"] == "coders-a"
    assert body[0]["kind"] == "succeeded"
    assert set(body[0]) == {
        "run_id", "coder_pool", "repo", "issue_number", "dispatched_at",
        "finished_at", "kind", "reason", "category", "requeue_attempt",
        "pr_lookup", "pr_number", "pr_url", "merged",
    }


def test_merged_is_published_as_a_real_boolean(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00",
             pr_lookup="found", pr_number=388, pr_url="https://example.invalid/388",
             merged=True)
    _archive(store, "B", finished="2026-09-20T09:00:00+00:00")

    by_id = {row["run_id"]: row for row in client.get("/api/history").json()}

    assert by_id["A"]["merged"] is True
    assert by_id["A"]["pr_number"] == 388
    assert by_id["B"]["merged"] is None
    assert by_id["B"]["pr_lookup"] == "none"


@pytest.mark.parametrize("query,expected", [
    ("?dir=asc", ["A", "B"]),
    ("?dir=ASC", ["A", "B"]),
    ("?dir=banana", ["B", "A"]),
    ("", ["B", "A"]),
])
def test_direction_is_ascending_only_for_asc(client, store, query, expected):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00")

    assert [r["run_id"] for r in client.get(f"/api/history{query}").json()] == expected


@pytest.mark.parametrize("limit,count", [
    ("?limit=2", 2), ("?limit=all", 3), ("?limit=abc", 3),
    ("?limit=0", 3), ("?limit=-1", 3),
])
def test_limit_parsing(client, store, limit, count):
    for name, hour in (("A", 10), ("B", 11), ("C", 12)):
        _archive(store, name, finished=f"2026-09-20T{hour}:00:00+00:00")

    assert len(client.get(f"/api/history{limit}").json()) == count


def test_an_unknown_sort_column_is_not_an_error(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    response = client.get("/api/history?sort=nonsense")
    assert response.status_code == 200
    assert [r["run_id"] for r in response.json()] == ["A"]


def test_history_page_is_empty_and_friendly(client):
    response = client.get("/history")
    assert response.status_code == 200
    assert "No released runs yet" in response.text


def test_history_page_lists_released_runs_newest_first(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00", number=9)
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00", number=350)

    body = client.get("/history").text

    assert body.index("B") < body.index("A")
    assert "andrewthetechie/jelly-swipe" in body
    assert "coders-a" in body
    assert "350" in body


def test_history_page_carries_no_refresh_and_no_script(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    body = client.get("/history").text
    assert "http-equiv=\"refresh\"" not in body
    assert "<script" not in body


def test_history_page_distinguishes_the_three_pr_states(client, store):
    _archive(store, "A", finished="2026-09-20T12:00:00+00:00",
             pr_lookup="found", pr_number=388,
             pr_url="https://github.com/andrewthetechie/jelly-swipe/pull/388",
             merged=True)
    _archive(store, "B", finished="2026-09-20T11:00:00+00:00", pr_lookup="failed")
    _archive(store, "C", finished="2026-09-20T10:00:00+00:00", pr_lookup="none")

    body = client.get("/history").text

    assert "https://github.com/andrewthetechie/jelly-swipe/pull/388" in body
    assert "#388" in body
    assert "unknown" in body


def test_the_queue_page_still_refreshes(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "http-equiv=\"refresh\"" in response.text


import re

from fabro_scheduler.store import HISTORY_SORT_COLUMNS


def test_every_linked_column_is_sortable(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    body = client.get("/history").text

    # Jinja autoescapes `&` in an attribute value, so the rendered href reads
    # `/history?sort=repo&amp;dir=desc`. Match the entity form, not the raw `&`.
    linked = set(re.findall(r"/history\?sort=([a-z_]+)&amp;", body))
    assert linked == HISTORY_SORT_COLUMNS


def test_the_active_column_link_flips_direction(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")

    body = client.get("/history?sort=repo&dir=desc").text

    assert "/history?sort=repo&amp;dir=asc" in body          # the active one flips
    assert "/history?sort=issue_number&amp;dir=desc" in body  # the others start descending


def test_limit_all_is_carried_through_the_links(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")

    everything = client.get("/history?limit=all").text
    assert "&amp;limit=all" in everything

    paged = client.get("/history?limit=5").text
    assert "&amp;limit=" not in paged


def test_the_sort_is_applied_before_the_limit(client, store):
    # C finished most recently but has the highest issue number. Sorting by issue
    # number ascending with limit=2 must show 9 and 10 -- not the two newest.
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00", number=9)
    _archive(store, "B", finished="2026-09-20T11:00:00+00:00", number=10)
    _archive(store, "C", finished="2026-09-20T12:00:00+00:00", number=350)

    body = client.get("/history?sort=issue_number&dir=asc&limit=2").text

    assert "<code>A</code>" in body
    assert "<code>B</code>" in body
    assert "<code>C</code>" not in body


def test_the_page_still_has_no_script(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    assert "<script" not in client.get("/history?sort=kind").text


def _nav(body: str) -> str:
    """Just the <nav> block, so an assertion cannot match a link elsewhere on the page."""
    found = re.search(r"<nav>(.*?)</nav>", body, re.DOTALL)
    assert found, "the page has no <nav> block"
    return found.group(1)


def test_the_queue_page_links_to_the_history(client):
    nav = _nav(client.get("/").text)
    assert 'href="/history"' in nav
    assert 'href="/"' not in nav          # the current page is not a link
    assert "<strong>Queue</strong>" in nav


def test_the_history_page_links_back_to_the_queue(client):
    nav = _nav(client.get("/history").text)
    assert 'href="/"' in nav
    assert 'href="/history"' not in nav
    assert "<strong>Run history</strong>" in nav


def test_both_pages_still_render(client):
    assert client.get("/").status_code == 200
    assert client.get("/history").status_code == 200
    assert "http-equiv=\"refresh\"" in client.get("/").text
    assert "http-equiv=\"refresh\"" not in client.get("/history").text


from fabro_scheduler.app import final_state_label


def _row(**overrides) -> dict:
    row = {
        "kind": "succeeded", "reason": None, "category": None,
        "requeue_attempt": 0, "pr_lookup": "none", "merged": None,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("overrides,expected", [
    ({"pr_lookup": "found", "merged": 1}, "merged"),
    ({"pr_lookup": "found", "merged": 0}, "not merged"),
    ({"pr_lookup": "none"}, "succeeded, no PR"),
    ({"pr_lookup": "failed"}, "succeeded, PR unknown"),
    ({"kind": "failed", "reason": "terminated", "category": "deterministic"},
     "failed (fabro restart)"),
    ({"kind": "failed", "reason": "cancelled"}, "cancelled"),
    ({"kind": "failed", "reason": "stage_failed", "category": "transient_infra"},
     "failed (infra)"),
    ({"kind": "failed", "reason": "stage_failed"}, "failed (stage_failed)"),
    ({"kind": "failed"}, "failed"),
    ({"kind": "dead"}, "dead"),
    ({"kind": "lost", "reason": "fabro 404"}, "lost (fabro 404)"),
    ({"kind": "wat", "reason": "huh"}, "wat/huh"),
])
def test_final_state_label(overrides, expected):
    assert final_state_label(_row(**overrides)) == expected


@pytest.mark.parametrize("attempts,suffix", [(0, ""), (1, " ×2"), (2, " ×3")])
def test_the_requeue_suffix_counts_attempts(attempts, suffix):
    row = _row(pr_lookup="found", merged=1, requeue_attempt=attempts)
    assert final_state_label(row) == f"merged{suffix}"


def test_the_page_renders_the_label_and_keeps_the_raw_facts(client, store):
    _archive(store, "A", finished="2026-09-20T12:00:00+00:00",
             pr_lookup="found", pr_number=388,
             pr_url="https://example.invalid/388", merged=True)

    body = client.get("/history").text

    assert "merged" in body
    assert 'title="succeeded' in body      # the raw kind is still one hover away
