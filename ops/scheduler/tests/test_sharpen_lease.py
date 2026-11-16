"""The sharpen lease (issue-sharpening 05, ADR 0033): dispatched ahead of every issue run, one
at a time, on the box's `-bg` model row, released from the run's own record, and kept out of
everything that belongs to an issue run (receipts, requeues, diversity, the issue metrics)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fabro_scheduler import dispatch as dispatch_module
from fabro_scheduler import reconcile as reconcile_module
from fabro_scheduler.background import BackgroundLane
from fabro_scheduler.config import CODER_LOOP_WORKFLOWS, load_config
from fabro_scheduler.dispatch import DispatchLoop
from fabro_scheduler.fabro import DispatchResult, FabroError, RunNotStarted
from fabro_scheduler.github import Issue
from fabro_scheduler.lease import Lease, LeaseStore
from fabro_scheduler.reconcile import _github_pass, reconcile_leases
from fabro_scheduler.sharpen import LABEL, MAX_ATTEMPTS, RETRY_SPACING
from fabro_scheduler.store import Store
from fabro_scheduler.workflow_version import ENTRYPOINTS

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"
JELLY = "andrewthetechie/jelly-swipe"   # priority 99
LAWN = "andrewthetechie/lawncare-saas"  # priority 30: ranks above JELLY
BOXES = ("coders-a=http://a.invalid/health,coders-b=http://a.invalid/health,"
         "coders-c=http://c.invalid/health,coders-d=http://c.invalid/health")
BG = {"coders-a": "coders-a-bg", "coders-b": "coders-a-bg", "coders-c": "coders-c-bg", "coders-d": "coders-c-bg"}


@dataclass
class FakeFabro:
    """The calls a dispatch and a release make, and a record of them."""

    runs: dict[str, dict] = field(default_factory=dict)
    events: dict[str, list[dict]] = field(default_factory=dict)
    jobs: list[dict] = field(default_factory=list)
    issue_runs: list[dict] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    job_error: Exception | None = None
    event_error: Exception | None = None
    n: int = 0

    def dispatch_job(self, **kwargs):
        if self.job_error is not None:
            raise self.job_error
        self.jobs.append(kwargs)
        self.n += 1
        return DispatchResult(f"SH{self.n}", "f" * 64, "a" * 40, True, "runnable")

    def dispatch(self, **kwargs):
        self.issue_runs.append(kwargs)
        self.n += 1
        return DispatchResult(f"IS{self.n}", "f" * 64, "a" * 40, True, "runnable")

    def box_stages(self, sha, workflow):
        return {"draft": "true", "recheck": "true"} if workflow == "issue-sharpen" else {"coder": "true"}

    def get_run(self, run_id):
        if run_id not in self.runs:
            raise FabroError("no such run", status_code=404)
        return self.runs[run_id]

    def iter_events(self, run_id):
        if self.event_error is not None:
            raise self.event_error
        return iter(self.events.get(run_id, []))

    def get_stages(self, run_id):
        return []

    def active_runs(self):
        return []

    def active_issue_claims(self):
        return set()

    def run_usage(self, run_id):
        return {}

    def cancel_run(self, run_id):
        self.cancelled.append(run_id)


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


@pytest.fixture
def writes(monkeypatch) -> list[tuple[str, str, int, str]]:
    """Every label write the dispatch loop and the release make."""
    seen: list[tuple[str, str, int, str]] = []
    for module in (dispatch_module, reconcile_module):
        monkeypatch.setattr(module, "add_label", lambda r, n, l, *a, **k: seen.append(("add", r, n, l)))
        monkeypatch.setattr(module, "remove_label", lambda r, n, l, *a, **k: seen.append(("remove", r, n, l)))
    return seen


@pytest.fixture
def rig(store, writes):
    fabro = FakeFabro()
    config = load_config(TRACKED_REPOS, env={})
    leases = LeaseStore(store)
    loop = DispatchLoop(config, store, leases, fabro, "ghp_test")
    return loop, fabro, leases, config


def put(store: Store, repo: str, number: int, *, labels=("agent",)) -> None:
    store.upsert_issue(Issue(repo, number, f"issue {number}", frozenset(labels), datetime.now(UTC), "aaaaaaaaaaaa"))


def row(store: Store, repo: str, number: int, *, put_issue: bool = True) -> int:
    if put_issue:
        put(store, repo, number, labels=("agent", LABEL))
    return store.sharpen_enqueue(repo, number, "aaaaaaaaaaaa", datetime.now(UTC))


# --- registration ---------------------------------------------------------------------


def test_the_run_is_a_registered_workflow_and_a_coder_loop():
    assert "issue-sharpen" in ENTRYPOINTS and ENTRYPOINTS["issue-sharpen"] == "workflows/issue-sharpen/workflow.fabro"
    assert "issue-sharpen" in CODER_LOOP_WORKFLOWS
    from fabro_scheduler.config import predicted_gap_minutes
    assert predicted_gap_minutes("issue-sharpen", "draft") == 0, "no gap-table rows: the lane fills nothing"


# --- dispatch -------------------------------------------------------------------------


def test_a_queued_row_takes_a_free_pool_with_the_documented_labels_and_inputs(store, rig, writes):
    loop, fabro, leases, _ = rig
    row(store, JELLY, 7)
    attempts = loop.tick()
    assert [(a.repo, a.issue_number, a.dispatched) for a in attempts] == [(JELLY, 7, True)]
    job = fabro.jobs[0]
    assert job["workflow"] == "issue-sharpen"
    assert job["inputs"] == {"issue_number": 7, "model_pool": f"{attempts[0].coder_pool}-bg"}
    assert job["coder_pool"] == attempts[0].coder_pool
    assert job["labels"] == {"lane": "coder", "box": attempts[0].coder_pool,
                             "workflow": "issue-sharpen", "issue": "7"}
    lease = leases.active()[0]
    assert (lease.target_kind, lease.workflow, lease.issue_number, lease.run_id) == ("sharpen", "issue-sharpen", 7, "SH1")
    assert lease.box_stages == {"draft": "true", "recheck": "true"}
    sharpen_row = store.sharpen_live_row(JELLY, 7)
    assert (sharpen_row["state"], sharpen_row["run_id"], sharpen_row["attempts"]) == ("running", "SH1", 1)
    assert writes == [], "no agent, agent-in-progress or receipt label"


def test_only_one_sharpening_is_active_across_the_fleet(store, rig):
    loop, fabro, leases, _ = rig
    row(store, JELLY, 7)
    row(store, JELLY, 8)
    loop.tick()
    loop.tick()
    loop.tick()
    assert len(fabro.jobs) == 1, "a second row waits while a lease is active"
    assert [lease.target_kind for lease in leases.active()] == ["sharpen"]


def test_a_sharpening_goes_before_an_issue_run_in_the_same_tick(store, rig):
    loop, fabro, leases, _ = rig
    put(store, LAWN, 1)
    row(store, JELLY, 7)
    attempts = loop.tick()
    assert [a.repo for a in attempts][:1] == [JELLY], "the sharpening dispatched first"
    assert len(fabro.jobs) == 1 and len(fabro.issue_runs) == 1
    pools = {lease.coder_pool: lease.target_kind for lease in leases.active()}
    assert sorted(pools.values()) == ["issue", "sharpen"]
    assert len(pools) == 2, "the next free pool took the issue"


def test_rows_are_taken_in_the_coder_queues_order(store, rig):
    loop, fabro, _, _ = rig
    row(store, JELLY, 3)    # lower number, lower-priority repo
    row(store, LAWN, 9)
    row(store, LAWN, 4)
    loop.tick()
    assert fabro.jobs[0]["repo"] == LAWN and fabro.jobs[0]["inputs"]["issue_number"] == 4


def test_a_row_waits_for_its_next_eligible_time(store, rig):
    loop, fabro, _, _ = rig
    rid = row(store, JELLY, 7)
    store.sharpen_defer(rid, datetime.now(UTC) + timedelta(minutes=5))
    loop.tick()
    assert fabro.jobs == []
    store.sharpen_defer(rid, datetime.now(UTC) - timedelta(seconds=1))
    loop.tick()
    assert len(fabro.jobs) == 1


def test_a_row_whose_issue_left_the_cache_is_not_dispatched(store, rig):
    loop, fabro, _, _ = rig
    row(store, JELLY, 7, put_issue=False)
    loop.tick()
    assert fabro.jobs == []


def test_no_free_or_undrained_pool_means_no_dispatch(store, rig):
    loop, fabro, _, config = rig
    row(store, JELLY, 7)
    drained = set(config.coder_pools)
    loop._drained_pools = lambda: drained
    loop.tick()
    assert fabro.jobs == []


def test_the_peak_block_does_not_stop_it_and_the_disk_gate_does(store, rig):
    loop, fabro, _, _ = rig
    put(store, LAWN, 1)
    row(store, JELLY, 7)
    loop._peak_pause = lambda now: True
    attempts = loop.tick()
    assert [a.repo for a in attempts] == [JELLY], "a sharpening spends no hosted credits, so it runs"
    assert fabro.issue_runs == [], "and the issue run still waits for the peak block to end"


def test_the_disk_gate_stops_it(store, rig):
    loop, fabro, _, _ = rig
    row(store, JELLY, 7)

    class Full:
        def sample(self):
            return True

    loop._disk = Full()
    assert loop.tick() == []
    assert fabro.jobs == []


def test_each_pool_maps_to_its_boxs_first_pool_bg_row(store):
    config = load_config(TRACKED_REPOS, env={"SCHEDULER_BACKGROUND": "on", "SCHEDULER_BOX_HEALTH": BOXES})
    lane = BackgroundLane(config, store, LeaseStore(store), FakeFabro())
    assert {pool: lane.model_pool(pool) for pool in BG} == BG


def test_a_dispatch_that_makes_no_run_leaves_the_row_queued_five_minutes_on(store, rig):
    loop, fabro, leases, _ = rig
    rid = row(store, JELLY, 7)
    fabro.job_error = FabroError("fabro said no", status_code=500)
    attempts = loop.tick()
    assert [a.failed_stage for a in attempts] == ["create"]
    after = store.sharpen_row(rid)
    assert (after["state"], after["attempts"]) == ("queued", 0)
    assert datetime.fromisoformat(after["next_eligible_at"]) - datetime.now(UTC) > timedelta(minutes=4)
    assert leases.active() == []


def test_a_run_that_was_created_and_not_started_counts_as_an_attempt(store, rig):
    loop, fabro, leases, _ = rig
    rid = row(store, JELLY, 7)
    fabro.job_error = RunNotStarted("created, never started", run_id="SHX")
    attempts = loop.tick()
    assert [(a.run_id, a.failed_stage) for a in attempts] == [("SHX", "start")]
    after = store.sharpen_row(rid)
    assert (after["state"], after["attempts"]) == ("queued", 1)
    assert datetime.fromisoformat(after["next_eligible_at"]) - datetime.now(UTC) < timedelta(minutes=6)
    assert leases.active() == []


# --- release --------------------------------------------------------------------------


def running(store: Store, leases: LeaseStore, fabro: FakeFabro, *, number: int = 7, attempts: int = 1,
            run_id: str = "SH1", status: dict | None = None) -> int:
    rid = row(store, JELLY, number)
    for _ in range(attempts):
        store.sharpen_claim(rid, run_id, datetime.now(UTC))
        if _ < attempts - 1:
            store.sharpen_retry(rid, next_eligible_at=datetime.now(UTC))
    leases.acquire(Lease("coders-a", JELLY, number, run_id, datetime.now(UTC) - timedelta(minutes=12),
                         workflow="issue-sharpen", target_kind="sharpen"))
    fabro.runs[run_id] = {"lifecycle": {"status": status or {"kind": "succeeded"}},
                          "timestamps": {"completed_at": datetime.now(UTC).isoformat()}}
    return rid


def record(**updates) -> list[dict]:
    return [{"event": "stage.completed", "node_id": "record", "properties": {"context_updates": updates}}]


def release(store, leases, fabro, config):
    return reconcile_leases(config, store, leases, fabro, "ghp_test")


@pytest.mark.parametrize("outcome", ["applied", "capped", "churn", "stale", "cannot_sharpen", "not_needed"])
def test_a_succeeded_run_ends_its_row_in_the_state_its_record_names(store, rig, writes, outcome):
    _, fabro, leases, config = rig
    rid = running(store, leases, fabro)
    fabro.events["SH1"] = record(sharpen_outcome=outcome, sharpen_rule="why", round=2, body_hash="aaaaaaaaaaaa",
                                 applied_hash="bbbbbbbbbbbb" if outcome == "applied" else "",
                                 recheck="ready" if outcome == "applied" else "", changes=3,
                                 transformations="to_post_deploy=1")
    actions = release(store, leases, fabro, config)
    after = store.sharpen_row(rid)
    assert [a.outcome for a in actions] == ["released"]
    assert (after["state"], after["round"], after["rule"], after["transformations"]) == (outcome, 2, "why", "to_post_deploy=1")
    if outcome == "applied":
        assert (after["applied_hash"], after["recheck"]) == ("bbbbbbbbbbbb", "ready")
    assert after["box_minutes"] is not None and after["box_minutes"] >= 11
    assert writes == [("remove", JELLY, 7, LABEL)], "the hold comes off and nothing else is written"
    assert leases.active() == []
    history = store.history_rows(sort="finished_at", descending=True, limit=5)
    assert [(h["target_kind"], h["workflow"]) for h in history] == [("sharpen", "issue-sharpen")]
    assert store.sharpen_live_row(JELLY, 7) is None


def test_a_failed_run_is_retried_thirty_minutes_on_and_the_third_failure_ends_the_row(store, rig, writes):
    _, fabro, leases, config = rig
    rid = running(store, leases, fabro, attempts=1, status={"kind": "failed", "reason": "workflow_error"})
    release(store, leases, fabro, config)
    after = store.sharpen_row(rid)
    assert after["state"] == "queued" and after["attempts"] == 1
    gap = datetime.fromisoformat(after["next_eligible_at"]) - datetime.now(UTC)
    assert RETRY_SPACING - timedelta(seconds=30) < gap <= RETRY_SPACING
    assert writes == [], "the issue stays held while the row is retried"
    assert leases.active() == []
    # The third failure (attempts == MAX_ATTEMPTS): the row fails and the hold is released.
    store2 = Store(":memory:")
    try:
        fabro2, leases2 = FakeFabro(), LeaseStore(store2)
        rid2 = running(store2, leases2, fabro2, attempts=MAX_ATTEMPTS,
                       status={"kind": "failed", "reason": "workflow_error"})
        release(store2, leases2, fabro2, config)
        end = store2.sharpen_row(rid2)
        assert (end["state"], end["attempts"]) == ("failed", MAX_ATTEMPTS)
        assert writes == [("remove", JELLY, 7, LABEL)]
    finally:
        store2.close()


def test_a_succeeded_run_with_no_usable_record_is_a_fault(store, rig):
    _, fabro, leases, config = rig
    rid = running(store, leases, fabro)
    fabro.events["SH1"] = record(sharpen_outcome="failed")
    release(store, leases, fabro, config)
    assert store.sharpen_row(rid)["state"] == "queued"
    rid = running(store, leases, fabro, number=8, run_id="SH2")
    fabro.events["SH2"] = []
    release(store, leases, fabro, config)
    assert store.sharpen_row(rid)["state"] == "queued"


@pytest.mark.parametrize("reason", ["terminated", "cancelled"])
def test_a_fabro_restart_or_an_operators_cancel_does_not_count(store, rig, reason):
    _, fabro, leases, config = rig
    rid = running(store, leases, fabro, status={"kind": "failed", "reason": reason})
    release(store, leases, fabro, config)
    after = store.sharpen_row(rid)
    assert (after["state"], after["attempts"], after["next_eligible_at"]) == ("queued", 0, None)


def test_a_lost_run_counts_as_a_failed_attempt(store, rig):
    _, fabro, leases, config = rig
    rid = running(store, leases, fabro)
    del fabro.runs["SH1"]
    actions = release(store, leases, fabro, config)
    assert [a.outcome for a in actions] == ["released"]
    after = store.sharpen_row(rid)
    assert (after["state"], after["attempts"]) == ("queued", 1)
    assert [h["kind"] for h in store.history_rows(sort="finished_at", descending=True, limit=5)] == ["lost"]


def test_a_record_that_cannot_be_read_keeps_the_lease(store, rig):
    _, fabro, leases, config = rig
    running(store, leases, fabro)
    fabro.event_error = FabroError("events down", status_code=502)
    actions = release(store, leases, fabro, config)
    assert [a.outcome for a in actions] == ["failed"]
    assert [lease.run_id for lease in leases.active()] == ["SH1"]


def test_a_run_still_going_is_adopted(store, rig):
    _, fabro, leases, config = rig
    running(store, leases, fabro, status={"kind": "running"})
    assert [a.outcome for a in release(store, leases, fabro, config)] == ["adopted"]


def test_cancelling_the_pool_returns_the_row_without_counting(store, rig):
    loop, fabro, leases, config = rig
    row(store, JELLY, 7)
    loop.tick()
    run_id = leases.active()[0].run_id
    fabro.cancel_run(run_id)
    fabro.runs[run_id] = {"lifecycle": {"status": {"kind": "failed", "reason": "cancelled"}}}
    release(store, leases, fabro, config)
    after = store.sharpen_live_row(JELLY, 7)
    assert (after["state"], after["attempts"]) == ("queued", 0)


# --- what a sharpen lease must not reach ---------------------------------------------


def test_receipt_recovery_ignores_the_lease(store, rig, monkeypatch):
    _, fabro, leases, config = rig
    running(store, leases, fabro, status={"kind": "running"})
    monkeypatch.setattr(reconcile_module, "fetch_in_progress", lambda repo, token: [7] if repo == JELLY else [])
    requeued: list[tuple[str, int]] = []
    monkeypatch.setattr(reconcile_module, "_requeue",
                        lambda store_, leases_, lease, token, **kw: requeued.append((lease.repo, lease.issue_number)))
    _github_pass(config, store, leases, fabro, "ghp_test", [])
    assert requeued == [(JELLY, 7)], "an issue run's receipt for #7 would have been live; a sharpen lease's is not"


def test_the_diversity_pass_and_the_running_set_ignore_the_lease(store, rig):
    loop, _, leases, _ = rig
    running(store, leases, FakeFabro(), status={"kind": "running"})
    assert loop._running_issues() == set()


def test_the_issue_run_metrics_do_not_count_it(store, rig):
    _, fabro, leases, config = rig
    running(store, leases, fabro)
    fabro.events["SH1"] = record(sharpen_outcome="applied", applied_hash="bbbbbbbbbbbb", recheck="ready")
    release(store, leases, fabro, config)
    kinds = {r["target_kind"] for r in store.history_rows(sort="finished_at", descending=True, limit=10)}
    assert kinds == {"sharpen"}
    with store._lock:
        counted = store._conn.execute(
            "SELECT COUNT(*) FROM run_history WHERE target_kind = 'issue'").fetchone()[0]
    assert counted == 0
    assert store.avg_run_duration(JELLY) is None, "the duration average reads issue runs only"
