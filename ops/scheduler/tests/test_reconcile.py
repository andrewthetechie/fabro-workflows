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
    ReleasePoller,
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


def test_an_operator_cancel_requeues_via_its_reason():
    # Decision 15: a cancel kills the run *and* requeues the issue. The reason is
    # in the projection and the category is only in the events tail (`canceled`, one
    # L), so reading the reason is both correct and free.
    run = {"lifecycle": {"status": {"kind": "failed", "reason": "cancelled"}}}
    assert should_requeue(run) is True
    # The events category, if it ever got read, agrees — but it is not what decides.
    assert should_requeue(
        {
            "lifecycle": {"status": {"kind": "failed", "reason": "cancelled"}},
            "_last_failure": {"category": "canceled"},
        }
    ) is True
    # The one-L/two-L trap in the other direction: a category-only predicate written
    # against `cancelled` would match nothing.
    assert should_requeue(
        {
            "lifecycle": {"status": {"kind": "failed"}},
            "_last_failure": {"category": "canceled"},
        }
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
def test_a_cancel_is_requeued_and_resets_the_issue_labels(config, store, leases, fabro):
    # A cancel arrives `failed/reason=cancelled` (two Ls); the events category is
    # `canceled` (one L). Decision 15 makes a cancel requeue the issue, and it has
    # to: the run exits before the graph's terminal label work, so without this the
    # issue keeps `agent-in-progress`, leaves the cache, and is invisible to both
    # `acquire` and the queue. The reason alone decides it, so the events endpoint is
    # never called — the mock below is there to prove that.
    leases.acquire(
        _lease(run_id="R1", queued_since=datetime(2026, 9, 18, 8, 0, tzinfo=UTC))
    )
    labels = _install_labels()
    respx.get(f"{FABRO_API}/runs/R1").mock(
        return_value=httpx.Response(
            200,
            json={"lifecycle": {"status": {"kind": "failed", "reason": "cancelled"}}},
        )
    )
    events = respx.get(f"{FABRO_API}/runs/R1/events").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert [(a.outcome) for a in actions] == ["released+requeued"]
    assert leases.active() == []
    # The labels are reset, so a later inventory poll picks the issue up again.
    assert ("DELETE", "agent-in-progress") in labels
    assert ("POST", "add") in labels
    restored = store.get_issue(FF, 7)
    assert restored is not None
    assert "agent" in restored.labels
    # The wait travels back with it, so the starvation ceiling is not reset.
    assert restored.first_seen == datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    assert events.call_count == 0


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


def _mock_active_runs(runs=None) -> None:
    """`GET /runs?status=...` — the live-run list recovery asks before un-labelling.

    Every `recover` test needs it now: the receipt scan treats a live fabro run as
    accounting for an issue just as a lease does, so an unmocked list is not a
    neutral default, it is the call that decides whether anything is an orphan.
    """
    respx.get(f"{FABRO_API}/runs").mock(
        return_value=httpx.Response(
            200, json={"data": runs or [], "meta": {"has_more": False}}
        )
    )


@respx.mock
def test_an_orphaned_receipt_is_unlabelled_and_requeued(config, store, leases, fabro):
    # The scheduler died between labelling and recording the lease: the issue wears
    # `agent-in-progress`, has no run behind it, and is invisible to `acquire`.
    # Recovery's GitHub pass restores it. (No leases, so the fabro pass is empty.)
    _install_labels()
    _mock_in_progress()
    _mock_active_runs()

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
    _mock_active_runs()

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
    _mock_active_runs()
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



@respx.mock
def test_a_hand_fired_runs_receipt_is_not_stripped(config, store, leases, fabro):
    # `ops/fabro-fire-backlog.sh` fires an issue by hand and takes **no lease** on
    # purpose (draft 10), for use when the scheduler is down — and the scheduler
    # coming back up is when this pass runs. The run's `claim` wrote
    # `agent-in-progress`, so on the lease test alone the receipt looks orphaned:
    # recovery would strip it and the loop would put a second run on work already
    # in progress. The live-run list is what tells them apart.
    _install_labels()
    _mock_in_progress([{"number": 9, "labels": [{"name": "agent-in-progress"}]}])
    _mock_active_runs(
        [
            {
                "id": "01MANUAL",
                "lifecycle": {"status": {"kind": "running"}},
                "repository": {"name": FF},
                "labels": {"source": "manual", "issue": "9"},
            }
        ]
    )

    report = recover(config, store, leases, fabro, GH_TOKEN)

    assert report.orphaned == []
    # Not back in the queue, so the dispatch loop cannot pick it up.
    assert store.get_issue(FF, 9) is None


@respx.mock
def test_a_fabro_that_cannot_list_runs_leaves_every_receipt_alone(
    config, store, leases, fabro
):
    # Fail closed. An empty claim set and "fabro is unreachable" are the same value
    # to the caller, and one of them un-labels every receipt in the factory. An
    # orphan left one more restart is cheap; two runs on one issue is not.
    _install_labels()
    _mock_in_progress([{"number": 9, "labels": [{"name": "agent-in-progress"}]}])
    respx.get(f"{FABRO_API}/runs").mock(return_value=httpx.Response(503, json={}))

    report = recover(config, store, leases, fabro, GH_TOKEN)

    assert report.orphaned == []
    assert store.get_issue(FF, 9) is None
    assert report.github_errors and report.github_errors[0].startswith("fabro:")


@respx.mock
def test_a_terminal_run_that_is_not_requeued_drops_its_cache_row(
    config, store, leases, fabro
):
    # The row is stale from the moment dispatch removed `agent`, and the 60s
    # inventory poll is what would eventually clear it — but the dispatch loop
    # ticks every 5s. Releasing the box without dropping the row is what let an
    # agent-shaped ending be dispatched a second time.
    store.upsert_issue(
        Issue(FF, 7, "t", frozenset({"agent"}), datetime(2026, 9, 18, 8, 0, tzinfo=UTC))
    )
    leases.acquire(_lease(run_id="R1"))
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

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert [action.outcome for action in actions] == ["released"]
    assert leases.active() == []
    assert store.get_issue(FF, 7) is None


@respx.mock
def test_an_unreadable_failure_category_keeps_the_lease(config, store, leases, fabro):
    # The events call is a second call and fails on its own. Letting it escape would
    # abandon every lease after this one on the tick, and at startup would cost the
    # boot its receipt scan.
    leases.acquire(_lease(run_id="R1"))
    leases.acquire(
        Lease("coders-b", FF, 8, "R2", datetime(2026, 9, 18, 12, 0, tzinfo=UTC))
    )
    respx.get(url__regex=RUNS).mock(
        return_value=httpx.Response(
            200,
            json={"lifecycle": {"status": {"kind": "failed", "reason": "stage_failed"}}},
        )
    )
    respx.get(f"{FABRO_API}/runs/R1/events").mock(
        return_value=httpx.Response(500, json={})
    )
    respx.get(f"{FABRO_API}/runs/R2/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "run.failed",
                        "properties": {
                            "failure": {"detail": {"category": INFRA_CATEGORY}}
                        },
                    }
                ]
            },
        )
    )
    _install_labels()

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    # R1 keeps its lease; R2 is still reconciled rather than abandoned with it.
    by_run = {action.lease.run_id: action.outcome for action in actions}
    assert by_run == {"R1": "failed", "R2": "released+requeued"}
    assert [lease.run_id for lease in leases.active()] == ["R1"]


# --- the periodic receipt scan --------------------------------------------------
#
# Draft 09 ran the receipt scan only at startup, on the premise that only a restart
# could orphan a receipt. 2026-09-19 disproved it: `mark_stuck` skipped its label
# work whenever `claim` had died before writing `issue.json`, so two issues kept
# `agent-in-progress` and silently left the queue while the scheduler was up.
#
# Running it on a timer instead needs the confirmation gate, because `_dispatch`
# writes the receipt several seconds before the run exists — it clones `main` in
# between — and a scan landing in that gap sees exactly what an orphan looks like.


def _poller(config, store, leases, fabro) -> ReleasePoller:
    return ReleasePoller(config, store, leases, fabro, GH_TOKEN)


@respx.mock
def test_the_periodic_scan_requeues_nothing_on_its_first_sighting(
    config, store, leases, fabro
):
    _install_labels()
    _mock_in_progress()
    _mock_active_runs()
    poller = _poller(config, store, leases, fabro)

    first = poller.receipt_tick()

    assert first.orphaned == []
    assert store.get_issue(FF, 9) is None


@respx.mock
def test_the_periodic_scan_requeues_on_the_second_consecutive_sighting(
    config, store, leases, fabro
):
    _install_labels()
    _mock_in_progress()
    _mock_active_runs()
    poller = _poller(config, store, leases, fabro)

    poller.receipt_tick()
    second = poller.receipt_tick()

    assert second.orphaned == [(FF, 9)]
    assert store.get_issue(FF, 9) is not None


@respx.mock
def test_a_receipt_that_gains_a_lease_between_scans_is_never_touched(
    config, store, leases, fabro
):
    # THE RACE the gate exists for. Dispatch labels the issue, then spends seconds
    # cloning `main` before the run and the lease exist. A scan in that gap sees a
    # receipt with no lease and no live run — indistinguishable from an orphan.
    # One scan later the lease is there, and nothing was written in between.
    _install_labels()
    _mock_in_progress([{"number": 7, "labels": [{"name": "agent-in-progress"}]}])
    _mock_active_runs()
    poller = _poller(config, store, leases, fabro)

    poller.receipt_tick()  # mid-dispatch: looks orphaned, is only a candidate
    leases.acquire(_lease(number=7, run_id="R1"))  # the dispatch completes
    second = poller.receipt_tick()

    assert second.orphaned == []
    assert store.get_issue(FF, 7) is None
    assert [lease.run_id for lease in leases.active()] == ["R1"]


@respx.mock
def test_a_hand_fired_run_is_still_protected_by_its_fabro_claim(
    config, store, leases, fabro
):
    # `ops/fabro-fire-backlog.sh` takes no lease on purpose, so the live-run check
    # is the only thing standing between it and a stripped receipt — twice over
    # now that the scan repeats every ten minutes rather than once per boot.
    _install_labels()
    _mock_in_progress([{"number": 9, "labels": [{"name": "agent-in-progress"}]}])
    _mock_active_runs(
        [
            {
                "id": "MANUAL",
                "repository": {"name": FF},
                "labels": {"source": "manual", "issue": "9"},
            }
        ]
    )
    poller = _poller(config, store, leases, fabro)

    assert poller.receipt_tick().orphaned == []
    assert poller.receipt_tick().orphaned == []
    assert store.get_issue(FF, 9) is None


@respx.mock
def test_a_candidate_lapses_when_its_repo_cannot_be_read(
    config, store, leases, fabro
):
    # A repo that errors contributes no candidates, so anything pending for it
    # lapses and needs two fresh sightings. That is the direction that does not
    # write on a guess: an orphan waits one more scan, which costs ten minutes.
    _install_labels()
    _mock_active_runs()
    seen = {"n": 0}

    def issues(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        if seen["n"] == 2:  # the second scan's jelly-swipe fetch
            return httpx.Response(502, json={"message": "bad gateway"})
        return httpx.Response(
            200, json=[{"number": 9, "labels": [{"name": "agent-in-progress"}]}]
        )

    respx.get(f"{GITHUB_API}/repos/{FF}/issues").mock(side_effect=issues)
    for slug in ("lawncare-saas", "womens-fantasy-sports", "writers-app"):
        respx.get(f"{GITHUB_API}/repos/andrewthetechie/{slug}/issues").mock(
            return_value=httpx.Response(200, json=[])
        )
    poller = _poller(config, store, leases, fabro)

    poller.receipt_tick()                      # sighting 1 -> candidate
    lapsed = poller.receipt_tick()             # the fetch fails -> candidate lapses
    third = poller.receipt_tick()              # sighting 1 again, not 2

    assert lapsed.orphaned == []
    assert lapsed.github_errors != []
    assert third.orphaned == []
    assert store.get_issue(FF, 9) is None

    fourth = poller.receipt_tick()
    assert fourth.orphaned == [(FF, 9)]


@respx.mock
def test_a_fabro_that_cannot_list_runs_stops_the_periodic_scan_too(
    config, store, leases, fabro
):
    # Same fail-closed rule as at startup: an empty claim set reads as "nothing is
    # live", which is the answer that un-labels everything.
    _install_labels()
    _mock_in_progress()
    respx.get(f"{FABRO_API}/runs").mock(return_value=httpx.Response(503, json={}))
    poller = _poller(config, store, leases, fabro)

    assert poller.receipt_tick().orphaned == []
    assert poller.receipt_tick().orphaned == []
    assert store.get_issue(FF, 9) is None
