"""The dispatch loop and the lease — no host, no container, no network.

`choose_next` is pure and asserted directly; `DispatchLoop.tick` is driven by hand,
so no thread is involved and the HTTP layer is `respx`. The cases worth having are
the ones whose failure is silent in production: a saturated same-repo run (the
saturation pass of a single busy repo), a second run on a busy box, and a dispatch
that fails after it has already written a label. The order of the requests is
asserted, not just their effect, because "label first, then create, then start" is
the contract draft 09's recovery reads.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from fabro_scheduler.app import build_app
from fabro_scheduler.config import load_config
from fabro_scheduler.dispatch import DispatchAttempt, DispatchLoop, choose_next
from fabro_scheduler.fabro import FabroClient
from fabro_scheduler.github import Issue
from fabro_scheduler.lease import Lease, LeaseConflict, LeaseStore
from fabro_scheduler.queue import QueueItem
from fabro_scheduler.reconcile import reconcile_leases
from fabro_scheduler.store import Store
from fabro_scheduler.workflow_version import WorkflowVersionError

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"

JELLY = "andrewthetechie/jelly-swipe"  # priority 99, environment_id "python"
LAWN = "andrewthetechie/lawncare-saas"  # priority 30, environment_id "python-node" — ranks above JELLY

FABRO_API = "http://10.10.0.32:32276/api/v1"
GITHUB_API = "https://api.github.com"
PULLS = re.compile(rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/pulls(?:\?.*)?$")
FABRO_TOKEN = "dev-token-do-not-echo"
VERSION_ID = "f" * 64
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

LABEL_POST = re.compile(
    rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/issues/\d+/labels$"
)
LABEL_DELETE = re.compile(
    rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/issues/\d+/labels/[^/]+$"
)


# --- fixtures ----------------------------------------------------------------------


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


@pytest.fixture
def loop(config, store, leases, fabro) -> DispatchLoop:
    return DispatchLoop(config, store, leases, fabro, "ghp_test")


def issue(repo: str, number: int) -> Issue:
    # Measured from the *real* clock: `tick()` reads `datetime.now`, so a fixed
    # `first_seen` in the past would put every item past the starvation ceiling and
    # the priority ordering these tests lean on would stop applying.
    return Issue(
        repo, number, f"issue {number}", frozenset({"agent"}), datetime.now(UTC)
    )


def seed(store: Store, repo: str, number: int) -> Issue:
    created = issue(repo, number)
    store.upsert_issue(created)
    return created


def qi(repo: str, number: int, priority: int = 0, waited: timedelta = timedelta(0)) -> QueueItem:
    return QueueItem(
        issue=Issue(repo, number, f"issue {number}", frozenset({"agent"}), NOW - waited),
        repo_priority=priority,
        override_rank=None,
        waited=waited,
    )


# --- the HTTP layer, recorded in order ---------------------------------------------


@dataclass
class Recorder:
    """Every request made, in order, with the JSON body that was sent.

    Order is the point for the label rules, and per-route filtering is the point
    everywhere else; a plain `respx.calls` gives neither.
    """

    calls: list[tuple[str, str, object]] = field(default_factory=list)

    def record(self, request: httpx.Request) -> None:
        body: object = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content.decode("utf-8", "replace")
        self.calls.append((request.method, request.url.path, body))

    def paths(self) -> list[str]:
        return [path for _, path, _ in self.calls]

    def made(self, method: str, path: str) -> list[tuple[str, str, object]]:
        return [
            call for call in self.calls if call[0] == method and call[1] == path
        ]

    def bodies(self, method: str, path: str) -> list[object]:
        return [body for _, _, body in self.made(method, path)]


def install_labels(
    recorder: Recorder,
    *,
    fail: Callable[[httpx.Request], httpx.Response | None] | None = None,
) -> None:
    """The two label endpoints. `fail` may refuse an individual request."""

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.record(request)
        if fail is not None:
            refusal = fail(request)
            if refusal is not None:
                return refusal
        return httpx.Response(200, json=[])

    respx.post(url__regex=LABEL_POST).mock(side_effect=handler)
    respx.delete(url__regex=LABEL_DELETE).mock(side_effect=handler)


def install_fabro(
    recorder: Recorder,
    *,
    run_ids: tuple[str, ...] = ("R1", "R2", "R3", "R4"),
    create_error: httpx.Response | None = None,
    start_error: httpx.Response | None = None,
) -> None:
    """Register, create, start — the three calls, each recording itself."""

    remaining = iter(run_ids)

    def register(request: httpx.Request) -> httpx.Response:
        recorder.record(request)
        return httpx.Response(201, json={"workflow_version_id": VERSION_ID})

    def create(request: httpx.Request) -> httpx.Response:
        recorder.record(request)
        if create_error is not None:
            return create_error
        return httpx.Response(
            201,
            json={"id": next(remaining), "lifecycle": {"status": {"kind": "submitted"}}},
        )

    def start(request: httpx.Request) -> httpx.Response:
        recorder.record(request)
        if start_error is not None:
            return start_error
        return httpx.Response(
            200, json={"lifecycle": {"status": {"kind": "runnable"}}}
        )

    respx.post(f"{FABRO_API}/workflow-versions").mock(side_effect=register)
    respx.post(f"{FABRO_API}/runs").mock(side_effect=create)
    respx.post(url__regex=rf"{re.escape(FABRO_API)}/runs/[^/]+/start").mock(
        side_effect=start
    )


def label_add(repo: str, number: int) -> str:
    return f"/repos/{repo}/issues/{number}/labels"


def label_remove(repo: str, number: int, label: str) -> str:
    return f"/repos/{repo}/issues/{number}/labels/{label}"


def receipts(recorder: Recorder, repo: str, number: int) -> list[tuple[str, str, object]]:
    """The attempts to *take* the receipt, as opposed to restore the queue label.

    Both are `POST .../labels`, distinguished only by the body, which is why the
    assertions that count dispatch attempts have to say which they mean.
    """
    return [
        call
        for call in recorder.made("POST", label_add(repo, number))
        if call[2] == {"labels": ["agent-in-progress"]}
    ]


# --- choose_next, pure -------------------------------------------------------------


def test_a_single_busy_repo_saturates_the_second_box():
    # Decision 6 as "prefer diversity, never idle a box": the same repo already has
    # a run in flight, but the free box is filled from that repo's next issue rather
    # than idled. This is the single-repo-workload case the box must never sit idle
    # for.
    q = [qi("o/a", 1, priority=0), qi("o/a", 2, priority=0)]  # same repo
    leases = [Lease("coders-a", "o/a", 1, "R1", NOW)]
    assert choose_next(
        q, leases, free_pools=["coders-b"], last_pool_for_repo=lambda r: None
    ) == (q[1], "coders-b")


def test_saturation_never_re_dispatches_the_running_issue():
    # The cache still holds the running issue too (a label write does not evict the
    # row until the 60s poll), so saturation must skip that exact repo#issue and
    # take the next one. Without the running-keys guard the very next tick would
    # start a duplicate run of #1.
    q = [qi("o/a", 1, priority=0), qi("o/a", 2, priority=0)]
    leases = [Lease("coders-a", "o/a", 1, "R1", NOW)]
    assert choose_next(
        q, leases, free_pools=["coders-b"], last_pool_for_repo=lambda r: None
    )[0].issue.number == 2


def test_when_every_queued_item_is_already_running_the_box_stays_free():
    # The only item left is itself the one in flight: saturation has nothing safe to
    # take, so it correctly does not double-dispatch #1, and the loop waits for the
    # 60s poll to refresh the label instead.
    q = [qi("o/a", 1, priority=0)]
    leases = [Lease("coders-a", "o/a", 1, "R1", NOW)]
    assert (
        choose_next(
            q, leases, free_pools=["coders-b"], last_pool_for_repo=lambda r: None
        )
        is None
    )


def test_soft_affinity_prefers_the_last_box_but_never_waits():
    q = [qi("o/a", 1, priority=0)]
    # affinity target is busy -> take the free one anyway
    assert choose_next(
        q, leases=[], free_pools=["coders-b"], last_pool_for_repo=lambda r: "coders-a"
    ) == (q[0], "coders-b")


def test_soft_affinity_wins_over_pool_order_when_the_box_is_free():
    q = [qi("o/a", 1)]
    assert choose_next(
        q,
        leases=[],
        free_pools=["coders-b", "coders-a"],
        last_pool_for_repo=lambda r: "coders-a",
    ) == (q[0], "coders-a")


def test_without_affinity_the_first_free_pool_is_used():
    q = [qi("o/a", 1)]
    assert choose_next(
        q, leases=[], free_pools=["coders-b", "coders-a"], last_pool_for_repo=lambda r: None
    ) == (q[0], "coders-b")


def test_a_busy_repo_is_skipped_rather_than_the_box_left_idle():
    # The whole reason the loop does not stop at the top-ranked item: a free box
    # and eligible work in another repo must be put together.
    q = [qi("o/a", 1, priority=0), qi("o/b", 2, priority=1)]
    leases = [Lease("coders-a", "o/a", 1, "R1", NOW)]
    assert choose_next(
        q, leases, free_pools=["coders-b"], last_pool_for_repo=lambda r: None
    ) == (q[1], "coders-b")


def test_no_free_pool_is_no_dispatch():
    assert (
        choose_next([qi("o/a", 1)], [], free_pools=[], last_pool_for_repo=lambda r: None)
        is None
    )


def test_no_work_is_no_dispatch():
    assert (
        choose_next([], [], free_pools=["coders-a"], last_pool_for_repo=lambda r: None)
        is None
    )


# --- the lease store ---------------------------------------------------------------


def test_acquire_is_exclusive_per_pool(leases):
    leases.acquire(Lease("coders-a", "o/a", 7, "R1", NOW))

    with pytest.raises(LeaseConflict) as caught:
        leases.acquire(Lease("coders-a", "o/b", 8, "R2", NOW))

    assert caught.value.held_by is not None
    assert caught.value.held_by.run_id == "R1"
    assert "o/a#7" in str(caught.value)
    assert [lease.run_id for lease in leases.active()] == ["R1"]


def test_the_same_run_cannot_hold_two_boxes(leases):
    # A different guard from the pool primary key, and a caller bug rather than a
    # race, so it raises rather than being quietly ignored.
    leases.acquire(Lease("coders-a", "o/a", 7, "R1", NOW))
    with pytest.raises(sqlite3.IntegrityError):
        leases.acquire(Lease("coders-b", "o/a", 7, "R1", NOW))


def test_free_pools_excludes_leased_and_drained_boxes(leases):
    all_pools = ("coders-a", "coders-b")

    assert leases.free_pools(all_pools, frozenset()) == ["coders-a", "coders-b"]

    leases.acquire(Lease("coders-a", "o/a", 7, "R1", NOW))
    assert leases.free_pools(all_pools, frozenset()) == ["coders-b"]
    assert leases.free_pools(all_pools, frozenset({"coders-b"})) == []
    # Draining one box does not touch the other, and order is `all_pools` order.
    assert leases.free_pools(all_pools, frozenset({"coders-a"})) == ["coders-b"]


def test_free_pools_keeps_the_configured_order(leases):
    assert leases.free_pools(("coders-a", "coders-b"), frozenset()) == [
        "coders-a",
        "coders-b",
    ]


def test_acquire_remembers_the_box_for_the_repo(leases):
    assert leases.last_pool_for_repo("o/a") is None

    leases.acquire(Lease("coders-b", "o/a", 7, "R1", NOW))

    assert leases.last_pool_for_repo("o/a") == "coders-b"
    assert leases.last_pool_for_repo("o/b") is None


def test_a_refused_lease_records_no_affinity(leases):
    leases.acquire(Lease("coders-a", "o/a", 7, "R1", NOW))
    with pytest.raises(LeaseConflict):
        leases.acquire(Lease("coders-a", "o/b", 8, "R2", NOW))

    assert leases.last_pool_for_repo("o/b") is None


def test_a_lease_survives_a_store_reopen(tmp_path):
    path = tmp_path / "scheduler.db"
    first = Store(path)
    LeaseStore(first).acquire(Lease("coders-a", "o/a", 7, "R1", NOW))
    first.close()

    second = Store(path)
    try:
        assert LeaseStore(second).active() == [Lease("coders-a", "o/a", 7, "R1", NOW)]
        assert LeaseStore(second).last_pool_for_repo("o/a") == "coders-a"
    finally:
        second.close()


# --- one tick, end to end ----------------------------------------------------------


@respx.mock
def test_one_dispatch_writes_the_labels_then_creates_and_starts_the_run(loop, store, leases):
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(recorder)
    seeded = seed(store, JELLY, 7)

    attempts = loop.tick()

    assert attempts == [
        DispatchAttempt(
            repo=JELLY,
            issue_number=7,
            coder_pool="coders-a",
            run_id="R1",
        )
    ]
    assert attempts[0].dispatched is True
    # The order *is* the contract. `agent-in-progress` is the durable receipt draft
    # 09's recovery reads, so it is written before any run exists; `agent` goes
    # second so an interruption in between leaves the issue visibly marked rather
    # than invisible to both `acquire` and this loop.
    assert [(method, path) for method, path, _ in recorder.calls] == [
        ("POST", label_add(JELLY, 7)),
        ("DELETE", label_remove(JELLY, 7, "agent")),
        ("POST", "/api/v1/workflow-versions"),
        ("POST", "/api/v1/runs"),
        ("POST", "/api/v1/runs/R1/start"),
    ]
    assert recorder.calls[0][2] == {"labels": ["agent-in-progress"]}
    assert recorder.bodies("POST", "/api/v1/runs")[0]["args"]["inputs"] == {
        "issue_number": 7,
        "coder_pool": "coders-a",
    }
    assert [lease.run_id for lease in leases.active()] == ["R1"]
    assert leases.active()[0].queued_since == seeded.first_seen


@respx.mock
def test_the_lease_keeps_the_items_wait_after_the_poll_drops_it(loop, store, leases):
    # The issue leaves the `agent` collection the moment the receipt is written, so
    # the next successful inventory fetch deletes its cache row — `first_seen` and
    # all. Draft 09's requeue has to put the *original* wait back, so the lease is
    # the only place it can live. The column is nullable and the dataclass field is
    # optional, so this test is what keeps it from quietly becoming `None` again.
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(recorder)
    seeded = seed(store, JELLY, 7)

    loop.tick()
    store.sync_repo(JELLY, [], '"e2"', datetime.now(UTC))  # what the 60s poll does

    assert store.get_issue(JELLY, 7) is None
    assert leases.active()[0].queued_since == seeded.first_seen


@respx.mock
def test_two_repos_are_dispatched_one_per_box(loop, store, leases):
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(recorder)
    seed(store, JELLY, 1)
    seed(store, LAWN, 2)

    attempts = loop.tick()

    assert [attempt.dispatched for attempt in attempts] == [True, True]
    assert {attempt.coder_pool for attempt in attempts} == {"coders-a", "coders-b"}
    assert {attempt.repo for attempt in attempts} == {JELLY, LAWN}
    assert {
        lease.coder_pool: lease.repo for lease in leases.active()
    } == {"coders-a": LAWN, "coders-b": JELLY}
    assert leases.free_pools(("coders-a", "coders-b"), frozenset()) == []

    # `environment_id` is never a parameter of the dispatch: it is the repo's row.
    # LAWN (priority 30) ranks above JELLY (priority 99), so it goes first.
    creates = recorder.bodies("POST", "/api/v1/runs")
    assert [body["environment_id"] for body in creates] == ["python-node", "python"]
    assert [body["target"]["repo"] for body in creates] == [LAWN, JELLY]
    assert [
        body["args"]["inputs"]["coder_pool"] for body in creates
    ] == ["coders-a", "coders-b"]


@respx.mock
def test_two_boxes_and_one_repo_fill_both_boxes(loop, store, leases):
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(recorder)
    seed(store, JELLY, 1)
    seed(store, JELLY, 2)

    attempts = loop.tick()

    # A single repo saturates both boxes rather than idling one: the first item is
    # top-ranked, the second fills the remaining box via the saturation pass.
    assert len(attempts) == 2
    assert attempts[0].issue_number == 1  # the top-ranked one, not just any
    assert {attempt.issue_number for attempt in attempts} == {1, 2}
    assert len(recorder.made("POST", "/api/v1/runs")) == 2
    assert {lease.coder_pool for lease in leases.active()} == {"coders-a", "coders-b"}
    assert leases.free_pools(("coders-a", "coders-b"), frozenset()) == []


@respx.mock
def test_a_single_repo_fills_both_boxes_then_the_box_parks(loop, store, leases):
    # The cache still holds both issues on the second tick (a label write does not
    # evict the local copy until the 60s poll), but both issues are now running, so
    # the saturation pass has nothing safe to take and the loop parks — it does not
    # double-dispatch. This is the same-repo case of `choose_next` returning None.
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(recorder)
    seed(store, JELLY, 1)
    seed(store, JELLY, 2)

    first = loop.tick()
    second = loop.tick()

    assert len(first) == 2
    assert {a.issue_number for a in first} == {1, 2}
    assert second == []
    assert len(recorder.made("POST", "/api/v1/runs")) == 2
    assert {lease.coder_pool for lease in leases.active()} == {"coders-a", "coders-b"}


@respx.mock
def test_the_loop_parks_itself_once_every_box_is_leased(loop, store):
    # What makes leaving this running tolerable before draft 09: two boxes means at
    # most two leases, so there is no way to leak a third run and nothing spins.
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(recorder)
    seed(store, JELLY, 1)
    seed(store, JELLY, 2)
    seed(store, LAWN, 3)
    seed(store, LAWN, 4)

    assert len(loop.tick()) == 2
    assert loop.tick() == []
    assert loop.tick() == []
    assert len(recorder.made("POST", "/api/v1/runs")) == 2


@respx.mock
def test_a_drained_box_takes_no_new_work(loop, store):
    # Draft 11's control, through the real `pool_state` table: draining excludes a
    # box from `free_pools` and does nothing else.
    store.set_drained("coders-a", True)
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(recorder)
    seed(store, JELLY, 1)
    seed(store, LAWN, 2)

    attempts = loop.tick()

    # LAWN (priority 30) ranks above JELLY (99), so it is the one dispatched to the
    # one free box.
    assert [attempt.coder_pool for attempt in attempts] == ["coders-b"]
    assert [body["target"]["repo"] for body in recorder.bodies("POST", "/api/v1/runs")] == [
        LAWN
    ]

    # Undraining needs no restart: the flag is read on every tick.
    store.set_drained("coders-a", False)
    attempts = loop.tick()

    assert [attempt.coder_pool for attempt in attempts] == ["coders-a"]
    assert [body["target"]["repo"] for body in recorder.bodies("POST", "/api/v1/runs")][-1] == (
        JELLY
    )


# --- failures leave nothing behind -------------------------------------------------


@respx.mock
def test_a_refused_create_restores_the_queue_label_and_records_no_lease(loop, store, leases):
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(
        recorder,
        create_error=httpx.Response(
            422, json={"errors": [{"detail": "unknown environment id `nope`"}]}
        ),
    )
    seed(store, JELLY, 7)

    attempts = loop.tick()

    assert len(attempts) == 1
    assert attempts[0].failed_stage == "create"
    assert attempts[0].run_id is None
    assert attempts[0].dispatched is False
    assert leases.active() == []
    # Everything the receipt did is undone, and in the direction that leaves the
    # issue back in the queue: `agent-in-progress` off, `agent` on.
    assert recorder.calls[-2:] == [
        ("DELETE", label_remove(JELLY, 7, "agent-in-progress"), None),
        ("POST", label_add(JELLY, 7), {"labels": ["agent"]}),
    ]


@respx.mock
def test_a_tree_that_cannot_be_fetched_rolls_the_labels_back(config, store, leases):
    # The failure between the receipt and the run: git is missing, the clone timed
    # out, `.fabro/` moved. Fabro is never called, so there is nothing to cancel —
    # and the labels still have to go back, or the issue is marked forever with no
    # run behind it.
    class NoTree:
        def clone(self, repo: str, ref: str):
            raise WorkflowVersionError(
                "git is not on PATH; the workflow tree cannot be fetched"
            )

    recorder = Recorder()
    install_labels(recorder)
    install_fabro(recorder)
    loop = DispatchLoop(
        config,
        store,
        leases,
        FabroClient(FABRO_API, FABRO_TOKEN, clone=NoTree().clone),
        "ghp_test",
    )
    seed(store, JELLY, 7)

    attempts = loop.tick()

    assert attempts[0].failed_stage == "create"
    assert attempts[0].run_id is None
    assert leases.active() == []
    assert recorder.made("POST", "/api/v1/runs") == []
    assert recorder.calls[-2:] == [
        ("DELETE", label_remove(JELLY, 7, "agent-in-progress"), None),
        ("POST", label_add(JELLY, 7), {"labels": ["agent"]}),
    ]


@respx.mock
def test_a_run_that_cannot_be_started_is_reported_with_its_id_and_leaves_no_lease(
    loop, store, leases
):
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(
        recorder,
        start_error=httpx.Response(
            409, json={"errors": [{"detail": "run is not in a startable state"}]}
        ),
    )
    seed(store, JELLY, 7)

    attempts = loop.tick()

    assert attempts[0].failed_stage == "start"
    # The run exists and will never execute, so its id is carried out for the
    # operator to cancel; draft 09 reconciles it automatically.
    assert attempts[0].run_id == "R1"
    assert attempts[0].dispatched is False
    assert leases.active() == []
    assert recorder.calls[-2:] == [
        ("DELETE", label_remove(JELLY, 7, "agent-in-progress"), None),
        ("POST", label_add(JELLY, 7), {"labels": ["agent"]}),
    ]


@respx.mock
def test_a_label_write_that_fails_creates_no_run_and_no_lease(loop, store, leases):
    recorder = Recorder()
    failed = {"done": False}

    def fail(request: httpx.Request) -> httpx.Response | None:
        if request.method == "POST" and not failed["done"]:
            failed["done"] = True
            return httpx.Response(
                403, json={"message": "Resource not accessible by integration"}
            )
        return None

    install_labels(recorder, fail=fail)
    install_fabro(recorder)
    seed(store, JELLY, 7)

    attempts = loop.tick()

    assert attempts[0].failed_stage == "labels"
    assert attempts[0].run_id is None
    assert leases.active() == []
    # No version registration, no run.
    assert recorder.made("POST", "/api/v1/runs") == []
    assert recorder.made("POST", "/api/v1/workflow-versions") == []
    # A timeout is not proof that nothing was written, so the rollback runs even
    # though the *first* write is the one that failed.
    assert recorder.calls == [
        ("POST", label_add(JELLY, 7), {"labels": ["agent-in-progress"]}),
        ("DELETE", label_remove(JELLY, 7, "agent-in-progress"), None),
        ("POST", label_add(JELLY, 7), {"labels": ["agent"]}),
    ]


@respx.mock
def test_a_failed_repo_is_backed_off_for_one_tick(loop, store):
    # LAWN (priority 30) is the top-ranked repo here, so it is the one whose label
    # write fails; a failing top-ranked repo must back off for this tick without
    # idling the box (JELLY, priority 99, still goes) and be retried next tick.
    recorder = Recorder()

    def fail(request: httpx.Request) -> httpx.Response | None:
        if request.method == "POST" and LAWN in request.url.path:
            return httpx.Response(403, json={"message": "Resource not accessible"})
        return None

    install_labels(recorder, fail=fail)
    install_fabro(recorder)
    seed(store, LAWN, 2)  # the one whose labels fail
    seed(store, JELLY, 1)

    attempts = loop.tick()

    # The free box is not left idle: the next-ranked item from another repo goes.
    assert [(a.repo, a.failed_stage) for a in attempts] == [
        (LAWN, "labels"),
        (JELLY, None),
    ]
    assert [body["target"]["repo"] for body in recorder.bodies("POST", "/api/v1/runs")] == [
        JELLY
    ]
    # Attempted once this tick, not in a tight loop. Counted by the receipt write
    # specifically: the rollback restores `agent` through the same POST route.
    assert len(receipts(recorder, LAWN, 2)) == 1

    loop.tick()
    # ...and tried again on the next tick, 5 seconds later.
    assert len(receipts(recorder, LAWN, 2)) == 2


@respx.mock
def test_a_failing_repo_in_every_tick_cannot_starve_the_others(loop, store):
    # writers-app (priority 20) is the top-ranked repo, so it is the always-failing
    # one: it must be attempted first, back off, and still not starve the lower-
    # priority repos (LAWN 30, JELLY 99), which fill the two boxes anyway.
    recorder = Recorder()

    def fail(request: httpx.Request) -> httpx.Response | None:
        if "andrewthetechie/writers-app" in request.url.path:
            return httpx.Response(403, json={"message": "Resource not accessible"})
        return None

    install_labels(recorder, fail=fail)
    install_fabro(recorder)
    seed(store, "andrewthetechie/writers-app", 3)
    seed(store, LAWN, 2)
    seed(store, JELLY, 1)

    attempts = loop.tick()

    assert [(a.repo, a.failed_stage) for a in attempts] == [
        ("andrewthetechie/writers-app", "labels"),
        (LAWN, None),
        (JELLY, None),
    ]


# --- the loop itself ---------------------------------------------------------------


@respx.mock
def test_ticking_by_hand_makes_no_request_when_there_is_no_work(loop, store):
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(recorder)

    assert loop.tick() == []
    assert recorder.calls == []


def test_the_default_cadence_is_five_seconds(loop):
    # The whole loop is one local SQLite read and, at most, one dispatch. Five
    # seconds is the contract; a longer interval is a slower factory and a shorter
    # one is GitHub traffic for nothing.
    assert loop.interval_seconds == 5.0


def test_start_and_stop_are_idempotent_and_safe_in_any_order(loop, store, leases):
    loop.stop()  # before any start
    loop.start()
    loop.start()
    loop.stop()
    loop.stop()
    assert not loop.running


def test_a_failing_tick_does_not_stop_the_loop(config, store, leases, fabro, monkeypatch):
    # An unhandled bug must not silently end all dispatch while /health keeps
    # answering `ok`, which is the failure mode this service exists to avoid.
    calls: list[int] = []

    def tick():
        calls.append(1)
        raise RuntimeError("boom")

    loop = DispatchLoop(
        config, store, leases, fabro, "ghp_test", interval_seconds=0.01
    )
    monkeypatch.setattr(loop, "tick", tick)

    loop.start()
    time.sleep(0.15)
    loop.stop()

    assert len(calls) >= 2


def test_the_loop_is_not_started_without_a_fabro_token(config, store, caplog, fake_checkout):
    with caplog.at_level(logging.ERROR):
        app = build_app(config, store, github_token="ghp_test", start_dispatch_loop=True)

    assert "FABRO_API_TOKEN is not set" in caplog.text
    assert "Automatic dispatch is off" in TestClient(app).get("/").text


def test_the_loop_is_not_started_without_a_github_token(
    config, store, fabro, caplog
):
    # Half-armed would be worse than unarmed: the first thing a dispatch does is a
    # label write, so without a token it would retry a doomed dispatch every 5s.
    with caplog.at_level(logging.ERROR):
        app = build_app(
            config,
            store,
            fabro_client=fabro,
            start_dispatch_loop=True,
        )

    assert "GITHUB_TOKEN is not set: the dispatch loop will not start" in caplog.text
    assert "Automatic dispatch is off" in TestClient(app).get("/").text


@respx.mock
def test_an_agent_shaped_ending_is_not_dispatched_again(config, store, leases, fabro, loop):
    """The whole loop, end to end: dispatch, terminal-agent-shaped, next tick.

    Asserted here and not only in `test_reconcile` because the defect was only
    visible where the two halves meet. Release and requeue were each right on their
    own; what was wrong was that releasing the box left the `issue_cache` row the
    5-second tick reads, so an ending the requeue rule had just refused to put back
    was dispatched again inside the 60 seconds before the inventory poll noticed.
    """
    recorder = Recorder()
    install_labels(recorder)
    install_fabro(recorder)
    seed(store, JELLY, 350)

    assert [a.run_id for a in loop.tick()] == ["R1"]
    assert len(leases.active()) == 1

    # `mark_stuck`: the issue keeps `agent-stuck` and waits for a human.
    respx.get(f"{FABRO_API}/runs/R1").mock(
        return_value=httpx.Response(
            200,
            json={"lifecycle": {"status": {"kind": "failed", "reason": "stage_failed"}}},
        )
    )
    respx.get(f"{FABRO_API}/runs/R1/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "run.failed",
                        "properties": {"failure": {"detail": {"category": "agent"}}},
                    }
                ]
            },
        )
    )
    respx.get(url__regex=PULLS).mock(return_value=httpx.Response(200, json=[]))
    reconcile_leases(config, store, leases, fabro, "ghp_test")
    assert leases.active() == []

    assert loop.tick() == []
