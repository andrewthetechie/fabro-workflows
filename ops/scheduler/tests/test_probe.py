"""The run probe: mapping a lease's run to its live task count and stage.

The probe reads two things through the fabro API — the current stage from
`GET /runs/{id}/stages` and the decomposed-task count from
`GET /runs/{id}/sandbox/file` (absolute sandbox paths, H6) — and caches them per
run. These tests drive `RunProbe.tick()` by hand with a fake fabro client,
exactly as `DispatchLoop.tick()` is tested elsewhere.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fabro_scheduler.lease import Lease, LeaseStore
from fabro_scheduler.probe import RunProgress, RunProbe
from fabro_scheduler.store import Store

TASKS = "/tmp/fabro/tasks.json"
INDEX = "/tmp/fabro/task_index"


def _lease(store: Store, run_id: str = "01MRUN") -> None:
    LeaseStore(store).acquire(
        Lease(
            coder_pool="coders-a",
            repo="andrewthetechie/writers-app",
            issue_number=42,
            run_id=run_id,
            dispatched_at=datetime.now(UTC),
        )
    )


class FakeFabro:
    """A fabro client that never touches the network.

    `files` maps an absolute sandbox path to its body, or omits it entirely to
    stand in for fabro's `404` (file not found) / `409` (no active sandbox),
    which `read_sandbox_file` turns into `None` — the probe reads that as "—".
    """

    def __init__(self, stages=None, files: dict[str, str] | None = None):
        self.stages = stages or []
        self.files = files or {}

    def get_stages(self, run_id):
        return self.stages

    def read_sandbox_file(self, run_id, path):
        return self.files.get(path)


def _stages() -> list[dict]:
    return [
        {"node_id": "claim", "visit": 1, "status": "succeeded", "started_at": "t1"},
        {"node_id": "coder", "visit": 1, "status": "running", "started_at": "t2"},
    ]


def test_probe_reads_task_count_and_current_stage(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # tasks.json has 3 entries, task_index 2 → 1 completed of 3 total.
    fabro = FakeFabro(
        stages=_stages(),
        files={TASKS: "[{},{},{}]", INDEX: "2"},
    )

    probe = RunProbe(fabro, LeaseStore(store))
    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert snap is not None
    assert snap.tasks_total == 3
    assert snap.tasks_completed == 1
    assert snap.stage_name == "coder"
    assert snap.stage_visit == 1
    assert snap.status == "running"


def test_probe_counts_completed_as_index_minus_one(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # index 6, total 10 → 5 completed of 10.
    probe = RunProbe(
        FakeFabro(
            stages=_stages(),
            files={TASKS: "[" + ",".join("{}" for _ in range(10)) + "]", INDEX: "6"},
        ),
        LeaseStore(store),
    )

    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert (snap.tasks_completed, snap.tasks_total) == (5, 10)


def test_probe_reports_no_tasks_before_decompose(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # No tasks.json yet (404) — the run is still in prep/decompose.
    probe = RunProbe(FakeFabro(stages=_stages(), files={}), LeaseStore(store))

    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert (snap.tasks_completed, snap.tasks_total) == (None, None)


def test_probe_reports_no_tasks_without_an_active_sandbox(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # 409 in fabro → read_sandbox_file returns None → "—", same as a missing file.
    probe = RunProbe(FakeFabro(stages=_stages(), files={}), LeaseStore(store))

    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert (snap.tasks_completed, snap.tasks_total) == (None, None)


def test_probe_defaults_index_to_zero_when_task_index_missing(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # tasks.json present but no task_index yet → 0 completed of 3.
    probe = RunProbe(
        FakeFabro(stages=_stages(), files={TASKS: "[{},{},{}]"}),
        LeaseStore(store),
    )

    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert (snap.tasks_completed, snap.tasks_total) == (0, 3)


def test_probe_reads_a_malformed_tasks_json_as_no_decompose(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # Not JSON, or not an array → (None, None).
    for bad in ("not json", "{}", '"a string"'):
        probe = RunProbe(
            FakeFabro(stages=_stages(), files={TASKS: bad}),
            LeaseStore(store),
        )
        probe.tick()
        snap = probe.snapshot("01MRUN")
        assert (snap.tasks_completed, snap.tasks_total) == (None, None)


def test_probe_clamps_completed_when_total_shrinks(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # index 8 but total consolidated to 3 → never show 7/3.
    probe = RunProbe(
        FakeFabro(stages=_stages(), files={TASKS: "[{},{},{}]", INDEX: "8"}),
        LeaseStore(store),
    )

    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert (snap.tasks_completed, snap.tasks_total) == (3, 3)


def test_probe_falls_back_when_no_stage_is_running(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    stages = [
        {"node_id": "claim", "visit": 1, "status": "succeeded", "started_at": "t1"},
        {"node_id": "prep", "visit": 1, "status": "succeeded", "started_at": "t2"},
    ]
    # tasks.json has 2 entries, no task_index yet → 0 completed of 2.
    probe = RunProbe(
        FakeFabro(stages=stages, files={TASKS: "[{},{}]"}),
        LeaseStore(store),
    )

    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert snap.stage_name == "prep"  # the most recent, even though finished
    assert (snap.tasks_completed, snap.tasks_total) == (0, 2)


def test_probe_prunes_a_released_lease(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store, run_id="01MRUN")
    probe = RunProbe(
        FakeFabro(files={TASKS: "[{},{}]", INDEX: "1"}),
        LeaseStore(store),
    )
    probe.tick()
    assert probe.snapshot("01MRUN") is not None

    # Dispatch elsewhere never touches this store again; release ends the lease.
    LeaseStore(store).release("01MRUN")
    probe.tick()

    assert probe.snapshot("01MRUN") is None
