"""Bump, drain and cancel — the operator's three controls (draft 11).

The page is the one surface in this service a human drives while the factory runs,
and there is no auth on it (decision 16), so three things are worth asserting
rather than assuming: that a bump really outranks both other ties (it is useless
otherwise), that **drain never destroys work** (the box's current run keeps
running), and that **cancel never releases a lease itself** — it asks fabro to stop
the run and lets draft 09's one release path observe the terminal state.

GitHub and fabro are faked with `respx`; SQLite is `tmp_path`; the dispatch loop is
driven by hand, so no thread is involved. The synthetic `repos.toml` below is
deliberately not the tracked one: the ranking tests need two known priorities and
two known repo names, and borrowing the real work list would make them depend on
its contents.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from fabro_scheduler.app import build_app
from fabro_scheduler.config import RepoConfig, load_config
from fabro_scheduler.dispatch import DispatchLoop
from fabro_scheduler.fabro import FabroClient
from fabro_scheduler.github import Issue
from fabro_scheduler.lease import Lease, LeaseStore
from fabro_scheduler.queue import build_queue
from fabro_scheduler.store import Store

FABRO_API = "http://10.10.0.32:32276/api/v1"
GITHUB_API = "https://api.github.com"
FABRO_TOKEN = "dev-token-do-not-echo"
VERSION_ID = "f" * 64
CEILING = timedelta(hours=4)

# Two repos, two known priorities. `o/urgent` is the better repo and `o/normal` is
# the one every bump has to beat; the names are not real repos on purpose.
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

LABEL_POST = re.compile(
    rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/issues/\d+/labels$"
)
LABEL_DELETE = re.compile(
    rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/issues/\d+/labels/[^/]+$"
)


# --- fixtures ----------------------------------------------------------------------


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "repos.toml"
    path.write_text(REPOS)
    return load_config(path, env={})


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


@pytest.fixture
def client(config, store) -> TestClient:
    """The read/unarmed app: no fabro client, so cancel is a 503 once it has a lease."""
    return TestClient(build_app(config, store, github_token="ghp_test"))


@pytest.fixture
def fabro(fake_checkout) -> FabroClient:
    return FabroClient(FABRO_API, FABRO_TOKEN, clone=fake_checkout.clone)


@pytest.fixture
def armed(config, store, fabro) -> TestClient:
    """The app with a fabro client, which is what the cancel route needs."""
    return TestClient(build_app(config, store, github_token="ghp_test", fabro_client=fabro))


@pytest.fixture
def loop(config, store, fabro) -> DispatchLoop:
    return DispatchLoop(config, store, LeaseStore(store), fabro, "ghp_test")


def issue(
    repo: str,
    number: int,
    *,
    waited: timedelta = timedelta(0),
    now: datetime | None = None,
) -> Issue:
    # Measured from the real clock: the app computes each wait from
    # `datetime.now`, so a fixed `first_seen` in the past would put every row past
    # the starvation ceiling and the tier assertions would stop measuring tiers.
    return Issue(
        repo,
        number,
        f"issue {number}",
        frozenset({"agent"}),
        (now or datetime.now(UTC)) - waited,
    )


def repo(name: str, priority: int) -> RepoConfig:
    return RepoConfig(name=name, priority=priority, environment_id="python")


def lease(pool: str = "coders-a", run_id: str = "R1") -> Lease:
    return Lease(
        coder_pool=pool,
        repo="o/normal",
        issue_number=2,
        run_id=run_id,
        dispatched_at=datetime.now(UTC),
    )


# --- ranking: the bump beats both other tiers --------------------------------------


def test_bump_outranks_both_ceiling_and_priority(client, store):
    store.upsert_issue(
        issue("o/urgent", 1, waited=timedelta(hours=9))  # past the ceiling, best repo
    )
    store.upsert_issue(issue("o/normal", 2))

    assert client.post("/api/queue/o%2Fnormal/2/bump").status_code == 200

    assert [i["number"] for i in client.get("/api/queue").json()] == [2, 1]


def test_build_queue_carries_the_override_rank_from_the_store(store):
    # The join between the two halves of the feature: the route writes the row and
    # `build_queue` has to read it, or the page shows an order the loop ignores.
    store.upsert_issue(issue("o/normal", 2))
    store.upsert_issue(issue("o/urgent", 1))
    store.set_override("o/normal", 2, -7)

    items = build_queue(
        [repo("o/urgent", -99), repo("o/normal", 0)], store, datetime.now(UTC), CEILING
    )

    assert [(i.issue.number, i.override_rank) for i in items] == [(2, -7), (1, None)]


def test_repeated_bumps_put_the_newest_first(store):
    store.upsert_issue(issue("o/urgent", 1))
    store.upsert_issue(issue("o/normal", 2))
    store.upsert_issue(issue("o/normal", 3))

    # Most recent wins, and the ranks descend, so the newest sort is on top.
    assert store.bump_override("o/urgent", 1) == -1
    assert store.bump_override("o/normal", 2) == -2
    assert store.bump_override("o/normal", 3) == -3

    items = build_queue(
        [repo("o/urgent", -99), repo("o/normal", 0)], store, datetime.now(UTC), CEILING
    )
    assert [i.issue.number for i in items] == [3, 2, 1]


def test_a_bump_does_not_pre_empt_a_running_lease(store):
    # "Next" is ordering only: it never touches a running lease. Under decision 6
    # saturation a bumped busy-repo issue still dispatches once a box frees, but
    # that is the dispatch half; this is the ranking half, which is where the bump
    # puts the item first for that same-repo next-dispatch.
    store.upsert_issue(issue("o/normal", 2))
    store.upsert_issue(issue("o/urgent", 1))
    store.bump_override("o/normal", 2)

    ranked = build_queue(
        [repo("o/urgent", -99), repo("o/normal", 0)], store, datetime.now(UTC), CEILING
    )

    assert [item.issue.number for item in ranked] == [2, 1]


# --- the store: overrides and drain state -----------------------------------------


def test_bump_is_cleared_on_dispatch(store):
    store.set_override("o/a", 5, -1)
    store.clear_override_on_dispatch("o/a", 5)
    assert store.get_override("o/a", 5) is None


def test_bump_and_drain_state_survive_a_reopen(tmp_path):
    # Both are operator state, not inventory: a restart that forgot a drained box
    # would quietly put new work straight back onto the instance the operator took
    # out of rotation.
    path = tmp_path / "scheduler.db"
    first = Store(path)
    first.set_drained("coders-b", True)
    first.bump_override("o/normal", 2)
    first.close()

    second = Store(path)
    try:
        assert second.drained_pools() == {"coders-b"}
        assert second.get_override("o/normal", 2) == -1
        # The other pool was never touched, so it is not drained.
        assert second.drained_pools() != {"coders-a", "coders-b"}
    finally:
        second.close()


def test_undrain_clears_the_flag_without_deleting_the_row(store):
    store.set_drained("coders-a", True)
    store.set_drained("coders-a", False)
    assert store.drained_pools() == set()
    assert store.get_override("o/a", 1) is None  # unrelated table, untouched


# --- POST /api/queue/{repo}/{issue}/bump ------------------------------------------


def test_bump_reports_the_rank_it_took(client, store):
    store.upsert_issue(issue("o/normal", 2))

    first = client.post("/api/queue/o%2Fnormal/2/bump")
    second = client.post("/api/queue/o/normal/2/bump")  # an unencoded slash too

    assert first.status_code == 200
    assert first.json() == {"override_rank": -1}
    assert second.json() == {"override_rank": -2}
    assert store.get_override("o/normal", 2) == -2


def test_bump_clears_the_mark_for_the_row_it_names_only(client, store):
    store.upsert_issue(issue("o/normal", 2))
    store.upsert_issue(issue("o/normal", 3))

    client.post("/api/queue/o%2Fnormal/2/bump")

    assert store.get_override("o/normal", 2) == -1
    assert store.get_override("o/normal", 3) is None


def test_bump_refuses_an_issue_that_is_not_queued(client):
    # An override for an item that is not in the cache is invisible on the page and
    # would never be cleared by a dispatch, so it is a refusal, not a silent write.
    response = client.post("/api/queue/o%2Fnormal/99/bump")

    assert response.status_code == 404
    assert "not in the queue" in response.json()["detail"]


def test_bump_canonicalises_the_repo_spelling(client, store):
    # `repo_named` matches case-insensitively because GitHub resolves `o/Repo` and
    # `o/repo` to one repository. Writing the other spelling would create an override
    # under a key no cached row has — a 200 that silently changes nothing.
    store.upsert_issue(issue("o/normal", 2))

    response = client.post("/api/queue/O%2FNormal/2/bump")

    assert response.status_code == 200
    assert store.get_override("o/normal", 2) == -1
    assert store.get_override("O/Normal", 2) is None


def test_bump_refuses_a_repo_that_is_not_schedulable(client, store):
    store.upsert_issue(issue("o/normal", 2))

    response = client.post("/api/queue/o%2Fnope/2/bump")

    assert response.status_code == 404
    assert "repos.toml" in response.json()["detail"]


# --- POST /api/pools/{pool}/drain and /undrain -------------------------------------


def test_drain_and_undrain_round_trip(client, store):
    drained = client.post("/api/pools/coders-a/drain")
    assert drained.status_code == 200
    assert drained.json() == {"drained": True}
    assert store.drained_pools() == {"coders-a"}

    undrained = client.post("/api/pools/coders-a/undrain")
    assert undrained.status_code == 200
    assert undrained.json() == {"drained": False}
    assert store.drained_pools() == set()


def test_drain_refuses_a_pool_that_is_not_configured(client, store):
    # A `pool_state` row for a name the dispatch loop never walks would report a
    # drain that does nothing, which is worse than a refusal.
    for pool in ("coders-c", "coders", ""):
        response = client.post(f"/api/pools/{pool}/drain")
        assert response.status_code in (404, 405), pool
    assert store.drained_pools() == set()


def test_undrain_refuses_a_pool_that_is_not_configured(client):
    assert client.post("/api/pools/coders-c/undrain").status_code == 404


# --- GET /api/pools -----------------------------------------------------------------


def test_api_pools_reports_every_pool_with_its_drain_flag_and_lease(client, store):
    LeaseStore(store).acquire(
        Lease(
            coder_pool="coders-b",
            repo="o/normal",
            issue_number=2,
            run_id="01MRUN",
            dispatched_at=datetime.now(UTC),
        )
    )
    store.set_drained("coders-a", True)

    body = client.get("/api/pools").json()

    # Every configured pool appears, in config order, whether or not it is leased:
    # an absent row and an idle box must not be the same answer, because only one
    # of them is what draft 12 alerts on.
    assert [row["coder_pool"] for row in body] == ["coders-a", "coders-b"]
    assert body[0]["drained"] is True
    assert body[0]["lease"] is None
    assert body[1]["drained"] is False
    assert body[1]["lease"] == {
        "repo": "o/normal",
        "issue_number": 2,
        "run_id": "01MRUN",
        "dispatched_at": body[1]["lease"]["dispatched_at"],
    }
    assert "T" in body[1]["lease"]["dispatched_at"]  # ISO-8601, not a raw datetime


def test_api_pools_is_all_null_leases_when_nothing_is_running(client):
    body = client.get("/api/pools").json()
    assert [(row["coder_pool"], row["drained"], row["lease"]) for row in body] == [
        ("coders-a", False, None),
        ("coders-b", False, None),
    ]


# --- POST /api/pools/{pool}/cancel -------------------------------------------------


def test_cancel_without_a_lease_is_409_and_does_nothing(client, store):
    # Note the app has no fabro client: the lease check comes first on purpose, so
    # "there is no run to cancel" wins over "no token", which is the true answer.
    response = client.post("/api/pools/coders-a/cancel")

    assert response.status_code == 409
    assert "no active lease" in response.json()["detail"]
    assert LeaseStore(store).active() == []


def test_cancel_refuses_a_pool_that_is_not_configured(client):
    assert client.post("/api/pools/coders-c/cancel").status_code == 404


@respx.mock
def test_cancel_asks_fabro_and_leaves_the_lease_alone(armed, store, caplog):
    LeaseStore(store).acquire(lease(pool="coders-a", run_id="R1"))
    cancel = respx.post(f"{FABRO_API}/runs/R1/cancel").mock(
        return_value=httpx.Response(
            200,
            json={"id": "R1", "lifecycle": {"status": {"kind": "failed", "reason": "cancelled"}}},
        )
    )

    with caplog.at_level(logging.INFO, logger="fabro_scheduler.app"):
        response = armed.post("/api/pools/coders-a/cancel")

    assert response.status_code == 200
    assert response.json() == {"cancelled_run_id": "R1"}
    assert cancel.call_count == 1
    # **Not** released here. A cancel is asynchronous for a live run (fabro answers
    # 202), so releasing now would free a box the worker is still using. Draft 09's
    # release poll is the one path that drops the lease, when fabro reports the run
    # terminal.
    assert [held.run_id for held in LeaseStore(store).active()] == ["R1"]
    assert any("asked to stop" in r.message for r in caplog.records)


@respx.mock
def test_cancel_takes_a_202_as_requested_not_as_done(armed, store):
    # A live run's cancel is durably recorded and returns 202; the run converges
    # later. The lease still has to wait for the poll.
    LeaseStore(store).acquire(lease(pool="coders-b", run_id="R9"))
    respx.post(f"{FABRO_API}/runs/R9/cancel").mock(
        return_value=httpx.Response(202, json={"id": "R9"})
    )

    response = armed.post("/api/pools/coders-b/cancel")

    assert response.status_code == 200
    assert response.json() == {"cancelled_run_id": "R9"}
    assert [held.run_id for held in LeaseStore(store).active()] == ["R9"]


@respx.mock
def test_cancel_propagates_fabros_conflict_and_keeps_the_lease(armed, store):
    # Fabro says the run already finished or was already cancelled. That is not a
    # failed cancel, but the box is still leased, so the honest answer is the 409
    # plus what to expect — not a claimed success.
    LeaseStore(store).acquire(lease(pool="coders-a", run_id="R1"))
    respx.post(f"{FABRO_API}/runs/R1/cancel").mock(
        return_value=httpx.Response(
            409, json={"errors": [{"detail": "run is not running"}]}
        )
    )

    response = armed.post("/api/pools/coders-a/cancel")

    assert response.status_code == 409
    assert "already over" in response.json()["detail"]
    assert "release" in response.json()["detail"]
    assert [held.run_id for held in LeaseStore(store).active()] == ["R1"]


@respx.mock
def test_cancel_answers_502_when_fabro_refuses_for_another_reason(armed, store):
    LeaseStore(store).acquire(lease(pool="coders-a", run_id="R1"))
    respx.post(f"{FABRO_API}/runs/R1/cancel").mock(
        return_value=httpx.Response(500, json={"errors": [{"detail": "boom"}]})
    )

    response = armed.post("/api/pools/coders-a/cancel")

    assert response.status_code == 502
    assert [held.run_id for held in LeaseStore(store).active()] == ["R1"]


def test_cancel_is_503_when_the_token_is_missing_and_a_lease_is_held(config, store):
    LeaseStore(store).acquire(lease(pool="coders-a", run_id="R1"))
    response = TestClient(build_app(config, store, github_token="ghp_test")).post(
        "/api/pools/coders-a/cancel"
    )

    assert response.status_code == 503
    assert "FABRO_API_TOKEN" in response.json()["detail"]


def test_cancel_never_echoes_the_token(armed, store):
    LeaseStore(store).acquire(lease(pool="coders-a", run_id="R1"))
    assert FABRO_TOKEN not in armed.post("/api/pools/coders-a/cancel").text


# --- every mutation is POST, and every mutation is logged ---------------------------


def test_every_mutation_route_refuses_a_get(client):
    # A GET is what a link prefetch, a browser address bar or a crawler sends, so
    # none of these may be reachable that way.
    for path in (
        "/api/queue/o%2Fnormal/2/bump",
        "/api/pools/coders-a/drain",
        "/api/pools/coders-a/undrain",
        "/api/pools/coders-a/cancel",
        "/api/dispatch-once",
    ):
        assert client.get(path).status_code == 405, path


def test_every_mutation_is_logged_with_what_changed(client, store, caplog):
    # There is no auth (decision 16), so there is no identity to attribute a change
    # to — the log line is the whole audit trail.
    store.upsert_issue(issue("o/normal", 2))

    with caplog.at_level(logging.INFO, logger="fabro_scheduler.app"):
        client.post("/api/queue/o%2Fnormal/2/bump")
        client.post("/api/pools/coders-a/drain")
        client.post("/api/pools/coders-a/undrain")

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "o/normal#2 bumped to rank -1" in logged
    assert "coders-a drained" in logged
    assert "coders-a undrained" in logged


# --- the dispatch loop honours drain and clears the override ------------------------


def install_labels() -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        label = request.url.path.rsplit("/", 1)[-1] if request.method == "DELETE" else "add"
        calls.append((request.method, label))
        return httpx.Response(200, json=[])

    respx.post(LABEL_POST).mock(side_effect=handler)
    respx.delete(LABEL_DELETE).mock(side_effect=handler)
    return calls


def install_fabro(run_ids: tuple[str, ...] = ("R1", "R2", "R3", "R4")) -> None:
    remaining = iter(run_ids)
    respx.post(f"{FABRO_API}/workflow-versions").mock(
        return_value=httpx.Response(201, json={"workflow_version_id": VERSION_ID})
    )
    respx.post(f"{FABRO_API}/runs").mock(
        side_effect=lambda request: httpx.Response(
            201,
            json={"id": next(remaining), "lifecycle": {"status": {"kind": "submitted"}}},
        )
    )
    respx.post(url__regex=rf"{re.escape(FABRO_API)}/runs/[^/]+/start").mock(
        return_value=httpx.Response(
            200, json={"lifecycle": {"status": {"kind": "runnable"}}}
        )
    )


def seed(store: Store, repo_name: str, number: int) -> None:
    store.upsert_issue(issue(repo_name, number))


@respx.mock
def test_a_bumped_issue_dispatches_next_and_the_override_is_gone(loop, store):
    install_labels()
    install_fabro()
    seed(store, "o/urgent", 1)  # priority -99, would rank first on its own
    seed(store, "o/normal", 2)
    store.bump_override("o/normal", 2)

    attempts = loop.tick()

    # A tick fills every free box, so both go — in rank order, the bumped one first.
    assert [(a.repo, a.issue_number) for a in attempts] == [
        ("o/normal", 2),
        ("o/urgent", 1),
    ]
    # Cleared on dispatch: it meant "next", not "forever".
    assert store.get_override("o/normal", 2) is None
    # The other item was never bumped, so nothing to clear.
    assert store.get_override("o/urgent", 1) is None


@respx.mock
def test_drain_stops_new_dispatch_and_undrain_resumes_without_a_restart(loop, store):
    install_labels()
    install_fabro()
    seed(store, "o/urgent", 1)
    seed(store, "o/normal", 2)
    store.set_drained("coders-a", True)

    first = loop.tick()

    # Only the drained box is excluded; the other takes the top-ranked item.
    assert [(a.coder_pool, a.repo) for a in first] == [("coders-b", "o/urgent")]

    store.set_drained("coders-a", False)
    second = loop.tick()

    # The flag is read fresh every tick, so no restart and no second step.
    assert [(a.coder_pool, a.repo) for a in second] == [("coders-a", "o/normal")]


@respx.mock
def test_a_bumped_issue_whose_repo_is_busy_does_not_idle_the_other_box(loop, store):
    install_labels()
    install_fabro()
    # `o/normal` already has a run in flight; `o/urgent` is free.
    LeaseStore(store).acquire(
        Lease(
            coder_pool="coders-a",
            repo="o/normal",
            issue_number=2,
            run_id="RUNNING",
            dispatched_at=datetime.now(UTC),
        )
    )
    seed(store, "o/normal", 2)
    seed(store, "o/urgent", 1)
    store.bump_override("o/normal", 2)

    attempts = loop.tick()

    # The bumped item ranks first, but its repo is busy, so the free box takes the
    # next-ranked item from another repo rather than idling.
    assert [(a.coder_pool, a.repo, a.issue_number) for a in attempts] == [
        ("coders-b", "o/urgent", 1)
    ]
    # A dispatch that did not happen does not consume the bump.
    assert store.get_override("o/normal", 2) == -1


@respx.mock
def test_a_drained_box_keeps_running_the_work_it_already_holds(loop, store):
    # The whole difference between drain and cancel (decision 15).
    install_labels()
    install_fabro()
    # The lease holds `o/urgent`, so `o/normal` is still eligible work — otherwise
    # an empty tick would prove nothing about drain.
    LeaseStore(store).acquire(
        Lease(
            coder_pool="coders-a",
            repo="o/urgent",
            issue_number=1,
            run_id="RUNNING",
            dispatched_at=datetime.now(UTC),
        )
    )
    store.set_drained("coders-a", True)
    seed(store, "o/normal", 2)

    attempts = loop.tick()

    assert [(a.coder_pool, a.repo) for a in attempts] == [("coders-b", "o/normal")]
    # Drain destroyed nothing: the lease it held is exactly where it was.
    assert [held.run_id for held in LeaseStore(store).active()] == ["RUNNING", "R1"]


# --- what the page renders ----------------------------------------------------------


def test_the_page_shows_the_override_marker_and_a_bump_control(client, store):
    store.upsert_issue(issue("o/normal", 2))
    store.set_override("o/normal", 2, -1)

    html = client.get("/").text

    assert "overridden" in html
    # The to-top control is the old "Next" in the new three-control layout.
    assert "/api/queue/o/normal/2/top" in html
    assert "/api/queue/o/normal/2/up" in html
    assert "/api/queue/o/normal/2/down" in html
