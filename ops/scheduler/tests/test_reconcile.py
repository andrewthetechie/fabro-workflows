"""Release, requeue and recovery — no host, no container, no network.

fabro and GitHub are faked with `respx`; SQLite lives in `tmp_path`; the loop body
(`reconcile_leases`, `recover`) is driven by hand, so no thread is involved. The
cases worth having are the ones whose failure is silent in production: an
infra-shaped failure that must requeue, an agent-shaped one that must not, a fabro
loss that requeues, a terminal success that only releases, and a GitHub receipt
with no lease behind it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from fabro_scheduler.config import load_config
from fabro_scheduler.fabro import FabroClient
from fabro_scheduler.github import Issue
from fabro_scheduler.lease import Lease, LeaseStore
from fabro_scheduler.reconcile import (
    INFRA_CATEGORY,
    TERMINAL,
    is_terminal,
    reconcile_leases,
    recover,
    should_requeue,
)
from fabro_scheduler.store import Store

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"
FF = "andrewthetechie/jelly-swipe"

FABRO_API = "http://10.10.0.32:32276/api/v1"
GITHUB_API = "https://api.github.com"
FABRO_TOKEN = "dev-token-do-not-echo"
GH_TOKEN = "ghp_test"

RUNS = re.compile(rf"{re.escape(FABRO_API)}/runs/(?P<id>[^/]+)$")
EVENTS = re.compile(rf"{re.escape(FABRO_API)}/runs/(?P<id>[^/]+)/events$")
LABEL_POST = re.compile(
    rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/issues/\d+/labels$"
)
LABEL_DELETE = re.compile(
    rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/issues/\d+/labels/[^/]+$"
)


@pytest.fixture
def config():
    return load_config(TRACKED_REPOS, env={})


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


@pytest.fixture
def leases(store) -> LeaseStore:
    return LeaseStore(store)


@pytest.fixture
def fabro(fake_checkout) -> FabroClient:
    return FabroClient(FABRO_API, FABRO_TOKEN, clone=fake_checkout.clone)


def _lease(repo: str = FF, number: int = 7, run_id: str = "R1", queued_since=None) -> Lease:
    return Lease(
        coder_pool="coders-a",
        repo=repo,
        issue_number=number,
        run_id=run_id,
        dispatched_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        queued_since=queued_since,
    )


def _run(**overrides) -> dict:
    run = {"id": "R1", "lifecycle": {"status": {"kind": "running"}}}
    run.update(overrides)
    return run


def _failed_run(reason: str | None = None, category: str | None = None) -> dict:
    status: dict = {"kind": "failed"}
    if reason is not None:
        status["reason"] = reason
    run: dict = {"id": "R1", "lifecycle": {"status": status}}
    if category is not None:
        run["_last_failure"] = {"category": category}
    return run


def _install_labels() -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        label = request.url.path.rsplit("/", 1)[-1] if request.method == "DELETE" else "add"
        calls.append((request.method, label))
        return httpx.Response(200, json=[])

    respx.post(url__regex=LABEL_POST).mock(side_effect=handler)
    respx.delete(url__regex=LABEL_DELETE).mock(side_effect=handler)
    return calls


# --- the predicates -------------------------------------------------------------


def test_terminal_is_exactly_three_kinds():
    assert TERMINAL == frozenset({"succeeded", "failed", "dead"})
    for kind in TERMINAL:
        assert is_terminal({"lifecycle": {"status": {"kind": kind}}}) is True
    for kind in ("submitted", "runnable", "running", "blocked", "paused"):
        assert is_terminal({"lifecycle": {"status": {"kind": kind}}}) is False
    # A bare run carries nothing, so it is not terminal.
    assert is_terminal({"id": "R1"}) is False


def test_only_infra_failures_requeue():
    INFRA = {
        "lifecycle": {"status": {"kind": "failed"}},
        "_last_failure": {"category": "transient_infra"},
    }
    AGENT = {
        "lifecycle": {"status": {"kind": "failed"}},
        "_last_failure": {"category": "agent"},
    }
    assert TERMINAL and INFRA_CATEGORY == "transient_infra"
    assert should_requeue(INFRA) is True
    assert should_requeue(AGENT) is False
    # A missing category is NOT infra — the fail-toward-dropping default.
    assert should_requeue({"lifecycle": {"status": {"kind": "failed"}}}) is False


def test_a_fabro_restart_requeues_via_its_reason():
    # The one infra failure whose *category* lies (it is `deterministic`): a fabro
    # restart ends every in-flight run `failed/terminated`. Requeueing on the
    # category alone would requeue nothing on the exact bounce we care about.
    run = {"lifecycle": {"status": {"kind": "failed", "reason": "terminated"}}}
    assert should_requeue(run) is True
    # An agent-shaped failure with a `terminated` word absent is not requeued.
    assert should_requeue(
        {"lifecycle": {"status": {"kind": "failed", "reason": "some other reason"}}}
    ) is False


# --- the store and the lease ----------------------------------------------------


def test_requeue_preserves_first_seen(store):
    original = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    store.upsert_issue(Issue("o/a", 7, "t", frozenset({"agent"}), first_seen=original))
    store.requeue("o/a", 7)
    assert store.get_issue("o/a", 7).first_seen == original


def test_requeue_restores_a_missing_row_with_the_lease_wait(store):
    # The real scenario: the poll dropped the cache row when the issue left the
    # `agent` collection, so `first_seen` has to come back off the lease.
    original = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    assert store.get_issue("o/a", 7) is None
    store.requeue("o/a", 7, first_seen=original)
    restored = store.get_issue("o/a", 7)
    assert restored is not None
    assert restored.first_seen == original
    assert "agent" in restored.labels


def test_release_returns_the_lease_and_frees_the_box(leases):
    leases.acquire(_lease(run_id="R1"))
    released = leases.release("R1")
    assert released is not None and released.run_id == "R1"
    assert leases.active() == []
    assert leases.free_pools(("coders-a", "coders-b"), frozenset()) == [
        "coders-a",
        "coders-b",
    ]


def test_release_of_an_unknown_run_id_is_a_noop(leases):
    assert leases.release("nope") is None


# --- reconcile_leases: one lease against fabro ----------------------------------


@respx.mock
def test_a_terminal_success_is_released_but_not_requeued(config, store, leases, fabro):
    leases.acquire(_lease(run_id="R1"))
    respx.get(f"{FABRO_API}/runs/R1").mock(
        return_value=httpx.Response(
            200, json={"lifecycle": {"status": {"kind": "succeeded", "reason": "completed"}}}
        )
    )

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert [(a.outcome) for a in actions] == ["released"]
    assert leases.active() == []
    # A successful run must not reappear in the queue (no `agent` label restored).
    assert store.get_issue(FF, 7) is None


@respx.mock
def test_a_fabro_restart_requeues_and_keeps_the_original_wait(config, store, leases, fabro):
    original = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    leases.acquire(_lease(run_id="R1", queued_since=original))
    labels = _install_labels()
    respx.get(f"{FABRO_API}/runs/R1").mock(
        return_value=httpx.Response(
            200,
            json={"lifecycle": {"status": {"kind": "failed", "reason": "terminated"}}},
        )
    )

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert [(a.outcome) for a in actions] == ["released+requeued"]
    assert leases.active() == []
    # The receipt is removed and the queue label restored.
    assert ("DELETE", "agent-in-progress") in labels
    assert ("POST", "add") in labels
    # And the item is back in the queue with its original wait — not a fresh one.
    assert store.get_issue(FF, 7).first_seen == original


@respx.mock
def test_a_transient_infra_failure_requeues_via_the_events_category(
    config, store, leases, fabro
):
    # A coder timeout ends `failed` with category `transient_infra`, which is only
    # in the events tail — the one classification that costs an extra call.
    leases.acquire(_lease(run_id="R1"))
    labels = _install_labels()
    respx.get(f"{FABRO_API}/runs/R1").mock(
        return_value=httpx.Response(
            200, json={"lifecycle": {"status": {"kind": "failed"}}}
        )
    )
    respx.get(f"{FABRO_API}/runs/R1/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "run.failed",
                        "properties": {
                            "failure": {
                                "detail": {"category": "transient_infra"}
                            }
                        },
                    }
                ]
            },
        )
    )

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert [(a.outcome) for a in actions] == ["released+requeued"]
    assert ("DELETE", "agent-in-progress") in labels
    assert leases.active() == []
    assert store.get_issue(FF, 7) is not None


@respx.mock
def test_a_cancel_is_released_but_not_requeued(config, store, leases, fabro):
    # A cancel arrives `failed/reason=cancelled`; the events category is `canceled`
    # (one L). Neither is infra-shaped, and a cancel also leaves the receipt
    # behind — which the *recovery* GitHub pass, not this pass, repairs.
    leases.acquire(_lease(run_id="R1"))
    respx.get(f"{FABRO_API}/runs/R1").mock(
        return_value=httpx.Response(
            200,
            json={"lifecycle": {"status": {"kind": "failed", "reason": "cancelled"}}},
        )
    )
    respx.get(f"{FABRO_API}/runs/R1/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "run.failed",
                        "properties": {"failure": {"detail": {"category": "canceled"}}},
                    }
                ]
            },
        )
    )

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert [(a.outcome) for a in actions] == ["released"]
    assert leases.active() == []
    # Not requeued: no cache row for the issue.
    assert store.get_issue(FF, 7) is None


@respx.mock
def test_a_run_fabro_has_lost_is_released_and_requeued(config, store, leases, fabro):
    leases.acquire(_lease(run_id="R1"))
    labels = _install_labels()
    respx.get(f"{FABRO_API}/runs/R1").mock(return_value=httpx.Response(404, json={}))

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert [(a.outcome) for a in actions] == ["released+requeued"]
    assert leases.active() == []
    assert ("DELETE", "agent-in-progress") in labels
    assert store.get_issue(FF, 7) is not None


@respx.mock
def test_a_still_running_run_keeps_its_lease(config, store, leases, fabro):
    leases.acquire(_lease(run_id="R1"))
    respx.get(f"{FABRO_API}/runs/R1").mock(
        return_value=httpx.Response(
            200, json={"lifecycle": {"status": {"kind": "blocked"}}}
        )
    )

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert [(a.outcome) for a in actions] == ["adopted"]
    assert [lease.run_id for lease in leases.active()] == ["R1"]
    assert store.get_issue(FF, 7) is None


@respx.mock
def test_a_transient_fabro_error_keeps_the_lease(config, store, leases, fabro):
    # A 5xx from fabro is not proof the run is over; wrongly releasing and
    # requeueing on it could put a second run on a live issue.
    leases.acquire(_lease(run_id="R1"))
    respx.get(f"{FABRO_API}/runs/R1").mock(
        return_value=httpx.Response(503, json={"detail": "boom"})
    )

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert [(a.outcome) for a in actions] == ["failed"]
    assert [lease.run_id for lease in leases.active()] == ["R1"]


# --- recover: the GitHub pass ---------------------------------------------------


def _mock_in_progress(
    in_progress: list[dict] | None = None,
    *,
    idle_slugs: tuple[str, ...] = ("lawncare-saas", "womens-fantasy-sports", "writers-app"),
) -> None:
    """Answer the four repos' `/issues` polls; only jelly-swipe carries work."""
    respx.get(f"{GITHUB_API}/repos/{FF}/issues").mock(
        return_value=httpx.Response(
            200,
            json=(
                in_progress
                if in_progress is not None
                else [{"number": 9, "labels": [{"name": "agent-in-progress"}]}]
            ),
        )
    )
    for slug in idle_slugs:
        respx.get(f"{GITHUB_API}/repos/andrewthetechie/{slug}/issues").mock(
            return_value=httpx.Response(200, json=[])
        )


@respx.mock
def test_an_orphaned_receipt_is_unlabelled_and_requeued(config, store, leases, fabro):
    # The scheduler died between labelling and recording the lease: the issue wears
    # `agent-in-progress`, has no run behind it, and is invisible to `acquire`.
    # Recovery's GitHub pass restores it. (No leases, so the fabro pass is empty.)
    _install_labels()
    _mock_in_progress()

    report = recover(config, store, leases, fabro, GH_TOKEN)

    assert report.orphaned == [(FF, 9)]
    assert store.get_issue(FF, 9) is not None
    assert leases.active() == []


@respx.mock
def test_an_issue_with_a_live_lease_is_left_alone(config, store, leases, fabro):
    # A live lease means the issue is being worked — its `agent-in-progress` is the
    # real receipt, not an orphan. With draft 10 landed, the lease names the issue
    # the run actually works, so this must not touch it.
    leases.acquire(_lease(run_id="R1"))
    respx.get(f"{FABRO_API}/runs/R1").mock(
        return_value=httpx.Response(
            200, json={"lifecycle": {"status": {"kind": "running"}}}
        )
    )
    _mock_in_progress([{"number": 7, "labels": [{"name": "agent-in-progress"}]}])

    report = recover(config, store, leases, fabro, GH_TOKEN)

    assert report.orphaned == []
    assert [lease.run_id for lease in leases.active()] == ["R1"]
    assert store.get_issue(FF, 7) is None


@respx.mock
def test_recover_is_idempotent(config, store, leases, fabro):
    # Running it twice changes nothing the second time: the orphan is already
    # re-labelled `agent` and back in the queue, so the second scan finds nothing
    # wearing `agent-in-progress`.
    _install_labels()
    seen = {"n": 0}

    def issues(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("labels") == "agent-in-progress":
            seen["n"] += 1
            if seen["n"] == 1:
                return httpx.Response(
                    200,
                    json=[{"number": 9, "labels": [{"name": "agent-in-progress"}]}],
                )
        return httpx.Response(200, json=[])

    respx.get(f"{GITHUB_API}/repos/{FF}/issues").mock(side_effect=issues)
    for slug in ("lawncare-saas", "womens-fantasy-sports", "writers-app"):
        respx.get(f"{GITHUB_API}/repos/andrewthetechie/{slug}/issues").mock(
            return_value=httpx.Response(200, json=[])
        )

    first = recover(config, store, leases, fabro, GH_TOKEN)
    second = recover(config, store, leases, fabro, GH_TOKEN)

    assert first.orphaned == [(FF, 9)]
    assert second.orphaned == []
    assert store.get_issue(FF, 9) is not None

