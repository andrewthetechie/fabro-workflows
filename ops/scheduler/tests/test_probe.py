"""The run probe: mapping a lease's run to its live task count and stage.

The probe reads two things that are awkward to fetch per page load — the
decomposed-task count from the sandbox filesystem (via docker exec over the
socket) and the current stage from the fabro API — and caches them per run. These
tests drive `RunProbe.tick()` by hand with fake fabro and docker objects, exactly
as `DispatchLoop.tick()` is tested elsewhere.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fabro_scheduler.docker import DockerClient, decode_docker_stream
from fabro_scheduler.lease import Lease, LeaseStore
from fabro_scheduler.probe import RunProgress, RunProbe, TASKS_CMD
from fabro_scheduler.store import Store


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
    """A fabro client that never touches the network."""

    def __init__(self, stages=None, sandbox_id="cid1"):
        self.stages = stages or []
        self.sandbox_id = sandbox_id
        self.get_run_calls = 0

    def get_stages(self, run_id):
        return self.stages

    def get_run(self, run_id):
        self.get_run_calls += 1
        return {
            "sandbox": {
                "instance": {"runtime": {"id": self.sandbox_id}},
            }
        }


class FakeDocker:
    def __init__(self, output: str = ""):
        self.output = output
        self.commands: list[list[str]] = []

    def exec(self, container_id: str, command: list[str], *, timeout: float = 10.0) -> str:
        self.commands.append(command)
        return self.output


def _stages() -> list[dict]:
    return [
        {"node_id": "claim", "visit": 1, "status": "succeeded", "started_at": "t1"},
        {"node_id": "coder", "visit": 1, "status": "running", "started_at": "t2"},
    ]


def test_probe_reads_task_count_and_current_stage(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # tasks.json length 3, task_index 2 → 1 completed of 3 total.
    docker = FakeDocker("3\n2\n")
    fabro = FakeFabro(stages=_stages())

    probe = RunProbe(fabro, LeaseStore(store), docker)
    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert snap is not None
    assert snap.tasks_total == 3
    assert snap.tasks_completed == 1
    assert snap.stage_name == "coder"
    assert snap.stage_visit == 1
    assert snap.status == "running"
    # The exec asked for the tasks.json length, as fabro-run-status.sh does.
    assert TASKS_CMD in docker.commands


def test_probe_counts_completed_as_index_minus_one(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # index 6, total 10 → 5 completed of 10.
    probe = RunProbe(FakeFabro(stages=_stages()), LeaseStore(store), FakeDocker("10\n6\n"))

    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert (snap.tasks_completed, snap.tasks_total) == (5, 10)


def test_probe_clamps_completed_when_total_shrinks(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # index 8 but total consolidated to 3 → never show 7/3.
    probe = RunProbe(FakeFabro(stages=_stages()), LeaseStore(store), FakeDocker("3\n8\n"))

    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert (snap.tasks_completed, snap.tasks_total) == (3, 3)


def test_probe_reports_no_tasks_before_decompose(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    # FakeDocker returns "" — tasks.json not present yet.
    probe = RunProbe(FakeFabro(stages=_stages()), LeaseStore(store), FakeDocker(""))

    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert snap.tasks_total is None
    assert snap.tasks_completed is None


def test_probe_falls_back_when_no_stage_is_running(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store)
    stages = [
        {"node_id": "claim", "visit": 1, "status": "succeeded", "started_at": "t1"},
        {"node_id": "prep", "visit": 1, "status": "succeeded", "started_at": "t2"},
    ]
    # tasks.json length 2, no task_index yet → 0 completed of 2.
    probe = RunProbe(FakeFabro(stages=stages), LeaseStore(store), FakeDocker("2\n"))

    probe.tick()

    snap = probe.snapshot("01MRUN")
    assert snap.stage_name == "prep"  # the most recent, even though finished
    assert (snap.tasks_completed, snap.tasks_total) == (0, 2)


def test_probe_prunes_a_released_lease(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    _lease(store, run_id="01MRUN")
    probe = RunProbe(FakeFabro(stages=_stages()), LeaseStore(store), FakeDocker("3"))
    probe.tick()
    assert probe.snapshot("01MRUN") is not None

    # Dispatch elsewhere never touches this store again; release ends the lease.
    LeaseStore(store).release("01MRUN")
    probe.tick()

    assert probe.snapshot("01MRUN") is None


def test_decode_docker_stream_strips_multiplexed_headers():
    # Frame: stdout type (1), length 2, payload b"3\n".
    payload = b"\x01\x00\x00\x00\x00\x00\x00\x02" b"3\n"
    assert decode_docker_stream(payload) == "3"


def test_decode_docker_stream_keeps_stderr_and_concatenates():
    # Two frames: stdout "3", then stderr "!" — both kept, headers dropped.
    frame1 = b"\x01\x00\x00\x00\x00\x00\x00\x01" b"3"
    frame2 = b"\x02\x00\x00\x00\x00\x00\x00\x01" b"!"
    assert decode_docker_stream(frame1 + frame2) == "3!"
