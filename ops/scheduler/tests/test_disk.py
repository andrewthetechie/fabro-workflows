"""The disk gate: free-space sampling and the hysteresis that prevents flapping.

Pure arithmetic over a stubbed `statvfs`, so this file needs no disk, no container
and no host. Issue #51: a full host used to keep the dispatch loop claiming issues
that could not start a run.
"""

from __future__ import annotations

from types import SimpleNamespace

from fabro_scheduler.disk import GIB, DiskGate


def fake_free(free_gb: float):
    """A statvfs callable for one path, returning `free_gb` bytes free."""
    return lambda _path: SimpleNamespace(f_bavail=int(free_gb * GIB), f_frsize=1)


def test_a_disk_below_the_minimum_gates_and_hysteresis_holds_it_there():
    state = {"free": 40 * GIB}
    gate = DiskGate(
        ["/disk"],
        min_free_gb=50,
        hysteresis_gb=10,
        statvfs=lambda _path: SimpleNamespace(f_bavail=state["free"], f_frsize=1),
    )

    assert gate.sample() is True
    assert gate.state()["gated"] is True
    assert gate.state()["free_gb"] == 40.0
    assert gate.state()["min_free_gb"] == 50
    assert gate.state()["hysteresis_gb"] == 10

    # Between the minimum and the resume point: still gated.
    state["free"] = 55 * GIB
    assert gate.sample() is True

    # At the target plus hysteresis: open again.
    state["free"] = 60 * GIB
    assert gate.sample() is False
    assert gate.state()["gated"] is False


def test_above_the_minimum_stays_open():
    gate = DiskGate(
        ["/disk"], min_free_gb=50, hysteresis_gb=10, statvfs=fake_free(100)
    )
    assert gate.sample() is False
    assert gate.state()["free_gb"] == 100.0


def test_the_gate_uses_the_fullest_of_its_paths():
    # `/tmp` and the fabro storage volume can be different filesystems; the gate is
    # the worst of them, not the first.
    free = {"/fabro-storage": 100, "/tmp": 40}
    gate = DiskGate(
        ["/fabro-storage", "/tmp"],
        min_free_gb=50,
        hysteresis_gb=10,
        statvfs=lambda path: fake_free(free[path])(path),
    )
    assert gate.sample() is True
    assert gate.state()["path"] == "/tmp"
    assert gate.state()["free_gb"] == 40.0


def test_an_unreadable_path_is_skipped_when_another_can_be_read():
    def statvfs(path: str):
        if path == "/missing":
            raise OSError("no such mount")
        return SimpleNamespace(f_bavail=100 * GIB, f_frsize=1)

    gate = DiskGate(
        ["/missing", "/tmp"], min_free_gb=50, hysteresis_gb=10, statvfs=statvfs
    )
    assert gate.sample() is False
    assert gate.state()["path"] == "/tmp"
