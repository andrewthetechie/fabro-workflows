"""The background lane (more-local-inference C7): no host, no container, no network.

Every decision is driven by hand, exactly as `test_dispatch.py` drives the primary loop:
an injected clock, a fake fabro client and a fake health client. The cases worth having
are the ones whose failure is silent in production — a box that starts a background run
inside a coder loop's box stage, a job picked for a gap it cannot fit, and a cancel that
is recorded as a failure instead of a requeue.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fabro_scheduler.background import (
    BoxHealth,
    BackgroundLane,
    Health,
    is_box_stage,
    plan_arm_true,
    running_node,
)
from fabro_scheduler.config import ConfigError, load_config
from fabro_scheduler.fabro import DispatchResult, build_job_intent, status_reason
from fabro_scheduler.lease import Lease, LeaseStore
from fabro_scheduler.store import Store

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"
COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yaml"

JELLY = "andrewthetechie/jelly-swipe"  # priority 99, environment_id "python"
BOXES = "coders-a=http://a.invalid/health,coders-b=http://b.invalid/health"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

# The box stages a `backlog` lease carries, as the generated manifest gives them (C1).
BACKLOG_STAGES = {
    "coder": "true",
    "rework_t1": "true",
    "resolve_merge": "true",
    "extra_decompose": "true",
    "plan": "plan-arm",
    "review_merge.refute_fix": "true",
    "review_merge.resolve_base": "true",
}


# --- fakes -------------------------------------------------------------------------


@dataclass
class FakeFabro:
    """The three fabro calls the lane makes, plus a record of what it was asked."""

    stages: dict[str, list[dict]] = field(default_factory=dict)
    runs: dict[str, dict] = field(default_factory=dict)
    active: list[dict] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    dispatched: list[dict] = field(default_factory=list)
    cancel_raises: Exception | None = None
    dispatch_raises: Exception | None = None
    next_run: int = 256

    def get_stages(self, run_id: str) -> list[dict]:
        return self.stages.get(run_id, [])

    def active_runs(self) -> list[dict]:
        return list(self.active)

    def get_run(self, run_id: str) -> dict:
        return self.runs[run_id]

    def cancel_run(self, run_id: str) -> None:
        if self.cancel_raises is not None:
            raise self.cancel_raises
        self.cancelled.append(run_id)

    def dispatch_job(self, **kwargs) -> DispatchResult:
        if self.dispatch_raises is not None:
            raise self.dispatch_raises
        self.dispatched.append(kwargs)
        run_id = f"bg{self.next_run}"
        self.next_run += 1
        return DispatchResult(
            run_id=run_id,
            workflow_version_id="f" * 64,
            commit_sha="a" * 40,
            version_reused=True,
            status="runnable",
        )


class FakeHealth:
    def __init__(self, readings: dict[str, Health] | None = None) -> None:
        self.readings = readings or {}

    def read(self, box: str) -> Health | None:
        return self.readings.get(box)


IDLE = Health(busy=False, queued=0, idle_seconds=600.0)
BUSY = Health(busy=True, queued=0, idle_seconds=0.0)
JUST_IDLE = Health(busy=False, queued=0, idle_seconds=5.0)


def config(**env: str):
    return load_config(
        TRACKED_REPOS,
        env={"SCHEDULER_BACKGROUND": "on", "SCHEDULER_BOX_HEALTH": BOXES, **env},
    )


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


@pytest.fixture
def leases(store) -> LeaseStore:
    return LeaseStore(store)


def lane(config_, store, leases, fabro, health) -> BackgroundLane:
    return BackgroundLane(
        config_, store, leases, fabro, health=health, clock=lambda: NOW
    )


def held_lease(
    leases: LeaseStore,
    *,
    box: str = "coders-a",
    issue: int = 2,
    workflow: str = "backlog",
    box_stages: dict[str, str] | None = None,
    run_id: str = "coderun",
) -> Lease:
    lease = Lease(
        coder_pool=box,
        repo=JELLY,
        issue_number=issue,
        run_id=run_id,
        dispatched_at=NOW - timedelta(minutes=10),
        workflow=workflow,
        box_stages=BACKLOG_STAGES if box_stages is None else box_stages,
    )
    leases.acquire(lease)
    return lease


def enqueue(store: Store, *, kind: str = "box-noop", priority: int = 5, typical: int = 10,
            repo: str = JELLY, target: str = "", at: datetime = NOW,
            inputs: dict | None = None) -> int:
    job_id = store.enqueue_background_job(
        kind=kind,
        repo=repo,
        target=target,
        inputs=inputs or {},
        priority=priority,
        typical_box_minutes=typical,
        now=at,
    )
    assert job_id is not None
    return job_id


def running(store: Store, job_id: int, *, run_id: str = "bgrun", box: str = "coders-a") -> None:
    assert store.start_background_job(job_id, run_id=run_id, box=box, now=NOW)


# --- the pure decisions ------------------------------------------------------------


def test_the_arm_rule_matches_the_hook_and_rule_r13():
    assert [plan_arm_true(n) for n in range(8)] == [
        False, False, True, True, False, False, True, True
    ]
    assert is_box_stage({"plan": "plan-arm"}, 1, "plan") is False
    assert is_box_stage({"plan": "plan-arm"}, 2, "plan") is True
    assert is_box_stage({"coder": "true"}, 1, "coder") is True
    assert is_box_stage({}, 1, "coder") is False, "an absent stage is not a box stage"
    assert is_box_stage({"coder": "true"}, 1, None) is False, "between stages"


def test_the_running_stage_is_the_one_fabro_marks_running():
    stages = [
        {"node_id": "claim", "status": "succeeded"},
        {"node_id": "review", "status": "running"},
    ]
    assert running_node(stages) == "review"
    assert running_node([{"node_id": "coder", "status": "succeeded"}]) is None


def test_health_parses_the_two_fields_the_contract_names():
    assert Health.parse({"busy": False, "queued": 0, "idle_seconds": 42.0}) == IDLE.__class__(
        busy=False, queued=0, idle_seconds=42.0
    )
    assert Health.parse({"busy": False, "queued": 1}) == Health(False, 1, 0.0)
    assert Health.parse({"queued": 0}) is None
    assert Health.parse("nope") is None
    assert Health(False, 0, 99.0).idle is True
    assert Health(True, 0, 99.0).idle is False


def test_status_reason_reads_the_projection():
    assert status_reason({"lifecycle": {"status": {"kind": "failed", "reason": "cancelled"}}}) == (
        "cancelled"
    )
    assert status_reason({"lifecycle": {"status": {"kind": "succeeded"}}}) is None
    assert status_reason({}) is None


def test_a_job_intent_carries_the_pool_and_the_four_labels():
    intent = build_job_intent(
        workflow_version_id="v" * 64,
        repo=JELLY,
        environment_id="python",
        inputs={"nothing": "here"},
        labels={"lane": "background", "box": "coders-a", "job": "box-noop"},
        coder_pool="coders-a-bg",
    )
    assert intent["target"] == {"kind": "git", "repo": JELLY, "branch": "main"}
    assert intent["args"]["inputs"] == {"nothing": "here", "coder_pool": "coders-a-bg"}
    assert intent["args"]["labels"] == {
        "source": "scheduler",
        "lane": "background",
        "box": "coders-a",
        "job": "box-noop",
    }


# --- the six conditions, one at a time ---------------------------------------------


def check(store, leases, fabro, health, config_) -> BackgroundLane:
    return lane(config_, store, leases, fabro, health)


def test_a_coder_loop_in_a_hosted_stage_leaves_the_gap_to_the_lane(store, leases):
    held_lease(leases)
    fabro = FakeFabro(stages={"coderun": [{"node_id": "review", "status": "running"}]})
    fabro.active = [{"id": "coderun"}]
    enqueue(store, typical=5)
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    starts = lane_.fill(NOW)
    assert [start.box for start in starts] == ["coders-a"]
    assert fabro.dispatched[0]["coder_pool"] == "coders-a-bg"


def test_a_coder_loop_in_a_box_stage_blocks_the_start(store, leases):
    held_lease(leases)
    fabro = FakeFabro(stages={"coderun": [{"node_id": "coder", "status": "running"}]})
    enqueue(store)
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    assert lane_.fill(NOW) == []
    assert fabro.dispatched == []


def test_a_coder_loop_between_stages_blocks_the_start(store, leases):
    """D7: a loop with no stage in `running` does not qualify this tick. The gap it is
    about to enter has not started yet, and a job picked now could land in the next one."""
    held_lease(leases)
    fabro = FakeFabro(stages={"coderun": [{"node_id": "review", "status": "succeeded"}]})
    enqueue(store)
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    assert lane_.fill(NOW) == []


def test_a_lease_with_no_known_box_stages_blocks_the_start(store, leases):
    """Fail closed: with the manifest unread, no stage can be shown *not* to be a box
    stage, and a background run inside one is what the lane exists to prevent."""
    held_lease(leases, box_stages={})
    fabro = FakeFabro(stages={"coderun": [{"node_id": "review", "status": "running"}]})
    enqueue(store)
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    assert lane_.fill(NOW) == []


def test_a_drained_box_is_never_filled(store, leases):
    store.set_drained("coders-a", True)
    fabro = FakeFabro()
    enqueue(store)
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    assert lane_.fill(NOW) == []


def test_a_busy_or_shortly_idle_box_is_not_filled(store, leases):
    enqueue(store)
    for reading in (BUSY, JUST_IDLE):
        fabro = FakeFabro()
        lane_ = check(store, leases, fabro, FakeHealth({"coders-a": reading}), config())
        assert lane_.fill(NOW) == [], reading


def test_the_switch_off_starts_nothing(store, leases):
    enqueue(store)
    fabro = FakeFabro()
    lane_ = check(
        store, leases, fabro, FakeHealth({"coders-a": IDLE}),
        config(SCHEDULER_BACKGROUND="off"),
    )
    assert lane_.fill(NOW) == []
    assert fabro.dispatched == []
    assert lane_.state(NOW)["switch"] == "off"


def test_the_run_slot_reserve_counts_every_active_run_pending_included(store, leases):
    """C7 condition 5: `max_concurrent_runs` minus every active run, `pending` included,
    must still be at least `bg_slot_reserve` (3 by default)."""
    enqueue(store, typical=5)
    # 8 slots; 5 active leaves exactly 3 free, which is enough.
    fabro = FakeFabro(active=[{"id": f"r{i}", "lifecycle": {"status": {"kind": "pending"}}}
                              for i in range(5)])
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}),
                  config(SCHEDULER_MAX_CONCURRENT_RUNS="8"))
    assert [start.box for start in lane_.fill(NOW)] == ["coders-a"]

    # 6 active leaves 2, which is not.
    fabro2 = FakeFabro(active=[{"id": f"r{i}"} for i in range(6)])
    store2 = store
    enqueue(store2, target="second")
    lane2 = check(store2, leases, fabro2, FakeHealth({"coders-a": IDLE}),
                  config(SCHEDULER_MAX_CONCURRENT_RUNS="8"))
    assert lane2.fill(NOW) == []
    assert fabro2.dispatched == []


def test_no_job_that_fits_means_no_start(store, leases):
    held_lease(leases)
    fabro = FakeFabro(stages={"coderun": [{"node_id": "review", "status": "running"}]})
    # A hosted stage of backlog predicts 8 minutes; a 10-minute job does not fit.
    enqueue(store, typical=10)
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    assert lane_.fill(NOW) == []
    # The same job fits a CI watch's 30 minutes.
    fabro.stages["coderun"] = [
        {"node_id": "review_merge.watch_checks", "status": "running"}
    ]
    assert [start.box for start in lane_.fill(NOW)] == ["coders-a"]


def test_a_box_with_no_coder_loop_has_the_whole_box(store, leases):
    fabro = FakeFabro()
    enqueue(store, typical=600)
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    assert [start.box for start in lane_.fill(NOW)] == ["coders-a"]
    assert lane_.fill(NOW) == [], "a second job waits for the first: one run per box"


def test_the_page_explains_each_blocked_box(store, leases):
    held_lease(leases)
    fabro = FakeFabro(stages={"coderun": [{"node_id": "coder", "status": "running"}]})
    enqueue(store)
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    views = {view.box: view for view in lane_.box_views(NOW)}
    assert views["coders-a"].blocked_by == "loop in a box stage"
    # No `/health` reading has been taken for coders-b in this test, and the page never
    # goes to the network: the cache is filled by the tick, and absent means unknown.
    assert views["coders-b"].blocked_by == "box health unknown"


# --- picking ------------------------------------------------------------------------


def test_the_pick_is_priority_then_age_then_fit(store, leases):
    # The store does not derive the priority; a producer passes it in from D10's order.
    # Three `box-noop` rows with different priorities test the pick rule without
    # inventing a job kind that has no producer yet.
    first = enqueue(store, priority=1, typical=5, at=NOW - timedelta(minutes=3))
    second = enqueue(store, priority=2, typical=5, at=NOW - timedelta(minutes=9), target="b")
    enqueue(store, priority=5, typical=5, at=NOW - timedelta(minutes=30), target="c")
    fabro = FakeFabro()
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    lane_.fill(NOW)
    running_jobs = [job["job_id"] for job in store.background_jobs() if job["state"] == "running"]
    assert running_jobs == [first], "priority beats age: pr-doctor (1) before issue-precheck (2)"
    assert store.background_job(second)["state"] == "queued"


def test_a_job_cancelled_three_times_needs_twice_the_gap(store, leases):
    job_id = enqueue(store, typical=10)
    for _ in range(3):
        running(store, job_id)
        store.requeue_background_job(job_id, now=NOW)
    held_lease(leases)
    fabro = FakeFabro(stages={"coderun": [{"node_id": "review", "status": "running"}]})
    # A hosted stage predicts 8 minutes; 2 × 10 does not fit.
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    assert lane_.fill(NOW) == []
    fabro.stages["coderun"] = [
        {"node_id": "review_merge.watch_checks", "status": "running"}
    ]
    assert [start.box for start in lane_.fill(NOW)] == ["coders-a"]


# --- eviction -----------------------------------------------------------------------


def test_a_background_run_is_evicted_when_the_loop_reaches_a_box_stage(store, leases):
    job_id = enqueue(store)
    held_lease(leases)
    running(store, job_id, run_id="bgrun", box="coders-a")
    fabro = FakeFabro(stages={"coderun": [{"node_id": "coder", "status": "running"}]})
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    evicted = lane_.evict(NOW)
    assert [e.run_id for e in evicted] == ["bgrun"]
    assert fabro.cancelled == ["bgrun"]
    assert lane_.evictions_today(NOW) == 1


def test_a_background_run_is_not_evicted_while_the_loop_is_hosted(store, leases):
    job_id = enqueue(store)
    held_lease(leases)
    running(store, job_id, run_id="bgrun", box="coders-a")
    fabro = FakeFabro(stages={"coderun": [{"node_id": "review", "status": "running"}]})
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    assert lane_.evict(NOW) == []
    assert fabro.cancelled == []


def test_a_cancel_that_cannot_be_asked_records_nothing(store, leases):
    from fabro_scheduler.fabro import FabroError

    job_id = enqueue(store)
    held_lease(leases)
    running(store, job_id, run_id="bgrun", box="coders-a")
    fabro = FakeFabro(
        stages={"coderun": [{"node_id": "coder", "status": "running"}]},
        cancel_raises=FabroError("409", status_code=409),
    )
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    assert lane_.evict(NOW) == []
    assert lane_.evictions_today(NOW) == 0


# --- completion ---------------------------------------------------------------------


def ended_run(kind: str, reason: str | None = None) -> dict:
    status: dict[str, object] = {"kind": kind}
    if reason is not None:
        status["reason"] = reason
    return {"id": "bgrun", "lifecycle": {"status": status}}


def test_a_succeeded_run_finishes_its_job(store, leases):
    job_id = enqueue(store)
    running(store, job_id)
    fabro = FakeFabro(runs={"bgrun": ended_run("succeeded")})
    lane_ = check(store, leases, fabro, FakeHealth(), config())
    assert [end.state for end in lane_.sync(NOW)] == ["done"]
    assert store.background_job(job_id)["state"] == "done"


def test_a_cancelled_run_requeues_its_job_and_counts_the_cancel(store, leases):
    job_id = enqueue(store)
    running(store, job_id)
    fabro = FakeFabro(runs={"bgrun": ended_run("failed", "cancelled")})
    lane_ = check(store, leases, fabro, FakeHealth(), config())
    assert [end.state for end in lane_.sync(NOW)] == ["queued"]
    row = store.background_job(job_id)
    assert (row["state"], row["cancels"], row["run_id"]) == ("queued", 1, None)


def test_a_run_fabro_terminated_requeues_without_counting_a_cancel(store, leases):
    """A fabro restart is not the job's fault; counting it would sideline every job the
    lane was running at that moment (C7 step 11)."""
    job_id = enqueue(store)
    running(store, job_id)
    fabro = FakeFabro(runs={"bgrun": ended_run("failed", "terminated")})
    lane_ = check(store, leases, fabro, FakeHealth(), config())
    assert [end.state for end in lane_.sync(NOW)] == ["queued"]
    assert store.background_job(job_id)["cancels"] == 0


def test_any_other_failure_fails_the_job_with_its_reason(store, leases):
    job_id = enqueue(store)
    running(store, job_id)
    fabro = FakeFabro(runs={"bgrun": ended_run("failed", "sandbox_start_failed")})
    lane_ = check(store, leases, fabro, FakeHealth(), config())
    assert [end.state for end in lane_.sync(NOW)] == ["failed"]
    assert store.background_job(job_id)["outcome"] == "failed/sandbox_start_failed"


def test_a_running_run_is_left_alone(store, leases):
    job_id = enqueue(store)
    running(store, job_id)
    fabro = FakeFabro(runs={"bgrun": {"lifecycle": {"status": {"kind": "running"}}}})
    lane_ = check(store, leases, fabro, FakeHealth(), config())
    assert lane_.sync(NOW) == []
    assert store.background_job(job_id)["state"] == "running"


# --- the dispatch shape ------------------------------------------------------------


def test_a_started_job_sends_its_inputs_pool_and_labels(store, leases):
    enqueue(store, inputs={"repo": JELLY})
    fabro = FakeFabro()
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    lane_.fill(NOW)
    call = fabro.dispatched[0]
    assert call["repo"] == JELLY
    assert call["environment_id"] == "python"
    assert call["coder_pool"] == "coders-a-bg"
    assert call["workflow"] == "box-noop"
    assert call["labels"] == {"lane": "background", "box": "coders-a", "job": "box-noop"}
    assert call["inputs"] == {"repo": JELLY}


def test_a_job_for_a_repo_that_is_no_longer_schedulable_is_skipped(store, leases):
    job_id = store.enqueue_background_job(
        kind="box-noop", repo="andrewthetechie/gone", target="", inputs={},
        priority=5, typical_box_minutes=10, now=NOW,
    )
    fabro = FakeFabro()
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    assert lane_.fill(NOW) == []
    assert store.background_job(job_id)["state"] == "skipped"
    assert fabro.dispatched == []


# --- making room for a coder loop (D8) ---------------------------------------------


def test_make_room_cancels_the_youngest_background_run_when_the_cap_is_full(store, leases):
    older = enqueue(store)
    younger = enqueue(store, target="b", at=NOW + timedelta(seconds=1))
    running(store, older, run_id="bg-old", box="coders-a")
    running(store, younger, run_id="bg-new", box="coders-b")
    fabro = FakeFabro(active=[{"id": f"r{i}"} for i in range(8)])
    lane_ = check(store, leases, fabro, FakeHealth(), config(SCHEDULER_MAX_CONCURRENT_RUNS="8"))
    evicted = lane_.make_room(NOW)
    assert evicted is not None and evicted.run_id == "bg-new"
    assert fabro.cancelled == ["bg-new"]


def test_make_room_does_nothing_while_a_slot_is_free(store, leases):
    job_id = enqueue(store)
    running(store, job_id, run_id="bg-old", box="coders-a")
    fabro = FakeFabro(active=[{"id": f"r{i}"} for i in range(7)])
    lane_ = check(store, leases, fabro, FakeHealth(), config(SCHEDULER_MAX_CONCURRENT_RUNS="8"))
    assert lane_.make_room(NOW) is None
    assert fabro.cancelled == []


# --- the store's own rules ---------------------------------------------------------


def again(store: Store) -> int | None:
    return store.enqueue_background_job(
        kind="box-noop", repo=JELLY, target="", inputs={}, priority=5,
        typical_box_minutes=10, now=NOW,
    )


def test_enqueue_is_idempotent_while_the_job_is_queued_or_running(store):
    first = enqueue(store)
    assert again(store) is None, "a queued job is not enqueued twice"
    running(store, first)
    assert again(store) is None, "a running job is not enqueued twice"
    store.finish_background_job(first, state="done", outcome="succeeded", now=NOW)
    assert again(store) is not None, "a finished job does not block a new one"


def test_a_background_job_never_enters_the_coder_accounting(store, leases):
    """C8, step 13: a background run has no lease, so it has no `run_history` row and
    never reaches `runs_to_summarize` — the only place `summary.CODER_MODEL_PREFIX`
    is applied."""
    job_id = enqueue(store)
    running(store, job_id, run_id="bgrun", box="coders-a")
    fabro = FakeFabro(runs={"bgrun": ended_run("succeeded")})
    lane_ = check(store, leases, fabro, FakeHealth(), config())
    lane_.sync(NOW)
    assert store.history_rows() == []
    assert store.runs_to_summarize(
        finished_before=NOW + timedelta(minutes=1),
        retry_before=NOW + timedelta(minutes=1),
        limit=50,
    ) == []


# --- the health client -------------------------------------------------------------


def test_the_health_client_reads_the_contract_fields_and_fails_closed():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ok.invalid":
            return httpx.Response(200, json={"busy": False, "queued": 0, "idle_seconds": 41.0})
        if request.url.host == "busy.invalid":
            return httpx.Response(200, json={"busy": True, "queued": 2, "idle_seconds": 0.0})
        return httpx.Response(500, text="boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    health = BoxHealth(
        {
            "coders-a": "http://ok.invalid/health",
            "coders-b": "http://busy.invalid/health",
            "coders-c": "http://down.invalid/health",
        },
        client=client,
    )
    assert health.read("coders-a") == Health(False, 0, 41.0)
    assert health.read("coders-b") == Health(True, 2, 0.0)
    assert health.read("coders-c") is None
    assert health.read("coders-z") is None, "a box outside the lane has no URL"


# --- the page and the API ----------------------------------------------------------


def test_the_state_document_carries_the_switch_boxes_and_jobs(store, leases):
    job_id = enqueue(store)
    running(store, job_id, run_id="bgrun", box="coders-a")
    fabro = FakeFabro(runs={"bgrun": {"lifecycle": {"status": {"kind": "running"}}}})
    lane_ = check(store, leases, fabro, FakeHealth({"coders-a": IDLE}), config())
    # One tick fills the health cache and leaves the job running; the page then reads it.
    lane_.tick(NOW)
    state = lane_.state(NOW)
    assert state["switch"] == "on"
    assert state["gap_seconds"] == 30
    assert state["slot_reserve"] == 3
    assert state["counts"]["running"] == 1
    boxes = {box["box"]: box for box in state["boxes"]}
    assert boxes["coders-a"]["running"]["run_id"] == "bgrun"
    assert boxes["coders-a"]["health"]["busy"] is False
    assert [job["job_id"] for job in state["jobs"]] == [job_id]


def test_box_noop_is_the_one_producer(store, leases):
    fabro = FakeFabro()
    lane_ = check(store, leases, fabro, FakeHealth(), config())
    job_id = lane_.enqueue_box_noop(JELLY, NOW)
    assert job_id is not None
    assert lane_.enqueue_box_noop(JELLY, NOW) is None, "idempotent while queued"
    row = store.background_job(job_id)
    assert (row["kind"], row["priority"], row["typical_box_minutes"]) == ("box-noop", 5, 10)


def test_box_noop_is_not_enqueued_when_its_kind_is_off(store, leases):
    import dataclasses

    fabro = FakeFabro()
    lane_ = check(store, leases, fabro, FakeHealth(), config())
    lane_._config = dataclasses.replace(lane_._config, bg_kinds=())
    assert lane_.enqueue_box_noop(JELLY, NOW) is None


# --- the compose file (C7 step 3, D12) ---------------------------------------------


def test_the_compose_file_carries_the_six_variables_with_their_defaults():
    text = COMPOSE.read_text()
    for name, default in (
        ("SCHEDULER_BACKGROUND", "off"),
        ("SCHEDULER_BG_GAP_S", "30"),
        ("SCHEDULER_BG_SLOT_RESERVE", "3"),
        ("SCHEDULER_MAX_CONCURRENT_RUNS", "8"),
        ("SCHEDULER_BG_KINDS", "box-noop"),
    ):
        pattern = rf'^\s+{name}: "\$\{{{name}:-{re.escape(default)}\}}"$'
        assert re.search(pattern, text, re.M), name
    # The two boxes' /health URLs, in the format the claim hook reads.
    assert re.search(
        r'^\s+SCHEDULER_BOX_HEALTH: "\$\{SCHEDULER_BOX_HEALTH:-'
        r"coders-a=http://10\.10\.0\.29:5300/health,coders-b=http://10\.10\.0\.56:5300/health\}\"$",
        text,
        re.M,
    ), "SCHEDULER_BOX_HEALTH"


# --- the HTTP surface -------------------------------------------------------------


def test_the_page_and_the_api_render_the_lane(tmp_path):
    from fastapi.testclient import TestClient

    from fabro_scheduler.app import build_app

    store = Store(tmp_path / "scheduler.db")
    try:
        cfg = config()
        fake = FakeFabro()
        lane_ = lane(cfg, store, LeaseStore(store), fake, FakeHealth({"coders-a": IDLE}))
        client = TestClient(
            build_app(cfg, store, fabro_client=fake, background_lane=lane_)
        )

        assert client.get("/").status_code == 200, "the Background strip renders"
        body = client.get("/api/background").json()
        assert body["switch"] == "on"
        assert {box["box"] for box in body["boxes"]} == {"coders-a", "coders-b"}
        assert body["kinds"] == ["box-noop"]

        created = client.post("/api/background/box-noop", json={"repo": JELLY})
        assert created.status_code == 200
        assert created.json() == {"job_id": created.json()["job_id"], "repo": JELLY, "kind": "box-noop"}
        again = client.post("/api/background/box-noop", json={"repo": JELLY})
        assert again.status_code == 409, "idempotent while the job is queued"
        missing = client.post("/api/background/box-noop", json={"repo": "nope/nope"})
        assert missing.status_code == 404
        assert [job["kind"] for job in client.get("/api/background").json()["jobs"]] == [
            "box-noop"
        ]
    finally:
        store.close()
