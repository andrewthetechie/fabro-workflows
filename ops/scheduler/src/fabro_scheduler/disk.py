"""The dispatch disk gate (issue #51): stop starting runs before the host fills.

On 2026-10-19 the host disk reached 100%, and the scheduler kept claiming issues
every 5-6 seconds. Each dispatch failed in `workflow_version.clone_fabro_tree`
with `OSError: [Errno 28] No space left on device`, and each left
`agent-in-progress` on an issue with no run behind it. The fix is two-part: the
gate below pauses new dispatch before that point, and `DispatchLoop._dispatch`
rolls the labels back on any pre-run failure.

**What is measured.** The scheduler container cannot see the host's `/`. The
compose file mounts the `fabro-storage` volume at `/fabro-storage:ro`, and the
gate samples that plus `tempfile.gettempdir()`. A writable container layer, the
fabro volume and Docker's overlay store all live on the host's Docker data-root
filesystem, so the two paths catch the same disk and a split mount. The method is
recorded in ADR 0032.

**Free space, not a percentage.** A writers-app run can write 25-60 GB, so a
percentage of a large disk can still leave too little room for one run. The
threshold is `SCHEDULER_DISPATCH_MIN_FREE_GB` (50 by default); dispatch resumes
only at the threshold plus `SCHEDULER_DISPATCH_DISK_HYSTERESIS_GB` (10), so a disk
hovering at the line does not flap the loop.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GIB = 1024 ** 3

# A writers-app run writes 25-60 GB. 50 is the issue's default: enough headroom for
# one more run, not so much that the gate waits until the disk is unusable.
DEFAULT_DISPATCH_MIN_FREE_GB = 50
# The band that stops a disk parked at the threshold from flapping dispatch on and off.
DEFAULT_DISPATCH_DISK_HYSTERESIS_GB = 10
# The fabro storage volume (mounted read-only by compose) and the scheduler's own
# temporary directory. Both are on the host's Docker data-root filesystem; two paths
# because a future deployment could put one of them on another disk.
DEFAULT_DISPATCH_DISK_PATHS = ("/fabro-storage", tempfile.gettempdir())


class DiskGate:
    """Free-space sampler with hysteresis.

    `sample()` is the only mutating call. It reads every configured path, takes the
    one with the least free space, and returns whether dispatch should be paused.
    A transition logs once — not once per 5-second tick — and only a state change
    writes a line.
    """

    def __init__(
        self,
        paths: Sequence[str | Path] = DEFAULT_DISPATCH_DISK_PATHS,
        *,
        min_free_gb: int = DEFAULT_DISPATCH_MIN_FREE_GB,
        hysteresis_gb: int = DEFAULT_DISPATCH_DISK_HYSTERESIS_GB,
        statvfs: Callable[[str], Any] = os.statvfs,
    ) -> None:
        self._paths = tuple(str(path) for path in paths)
        self._min_free_gb = min_free_gb
        self._hysteresis_gb = hysteresis_gb
        self._statvfs = statvfs
        self._gated = False
        self._free_gb: float | None = None
        self._path: str | None = None
        self._error: str | None = None
        self._unreadable_logged = False

    def sample(self) -> bool:
        """Read the filesystems and return True when dispatch must pause."""
        readings: list[tuple[int, str]] = []
        errors: list[str] = []
        for path in self._paths:
            try:
                stat = self._statvfs(path)
                block = getattr(stat, "f_frsize", 0) or getattr(stat, "f_bsize", 0)
                free = max(0, int(stat.f_bavail) * int(block))
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            readings.append((free, path))

        if not readings:
            self._error = "; ".join(errors) or "no paths configured"
            if not self._unreadable_logged:
                log.warning(
                    "disk gate: cannot read free space (%s); leaving dispatch %s",
                    self._error,
                    "paused" if self._gated else "open",
                )
                self._unreadable_logged = True
            return self._gated

        self._unreadable_logged = False
        self._error = None
        free_bytes, path = min(readings)
        free_gb = free_bytes / GIB
        self._free_gb = round(free_gb, 1)
        self._path = path

        # Resume only above the band; enter the gate as soon as free space is below
        # the minimum. A gate that just cleared therefore needs more than the minimum
        # to come back, which is what stops a disk parked at the line from flapping.
        # Compare the unrounded value: rounding 59.96 to 60.0 must not resume early.
        resume_gb = self._min_free_gb + self._hysteresis_gb
        if self._gated:
            if free_gb >= resume_gb:
                self._gated = False
                log.info(
                    "disk gate: %.1f GB free on %s (resume at %d GB); dispatch resumed",
                    self._free_gb,
                    path,
                    resume_gb,
                )
        elif free_gb < self._min_free_gb:
            self._gated = True
            log.warning(
                "disk gate: only %.1f GB free on %s (minimum %d GB, resume at %d GB); "
                "no new dispatch until space is back",
                self._free_gb,
                path,
                self._min_free_gb,
                resume_gb,
            )
        return self._gated

    def state(self) -> dict[str, object]:
        """The last sample, for `/health` and the queue page.

        Never samples: the 5-second loop is the sampler, so reading the page or a
        healthcheck does not write a gate transition or touch the disk.
        """
        return {
            "gated": self._gated,
            "free_gb": self._free_gb,
            "path": self._path,
            "min_free_gb": self._min_free_gb,
            "hysteresis_gb": self._hysteresis_gb,
            "paths": list(self._paths),
            "error": self._error,
        }
