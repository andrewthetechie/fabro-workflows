"""The run probe: cached per-lease task count and current stage for the page.

The queue page's "Coder instances" table shows two live facts about a running
run that fabro's run projection does not carry directly on the page render:

* the number of tasks the issue was decomposed into (`/tmp/fabro/tasks.json` —
  the same datum `ops/fabro-run-status.sh` reads), and
* the workflow stage currently in progress (`review_merge.validate` etc.), linked
  to `.../runs/{id}/stages/{node}@{visit}`.

Both are read through the fabro API — the current stage from `GET /runs/{id}/
/stages`, the task count from `GET /runs/{id}/sandbox/file?path=/tmp/fabro/
tasks.json` and its sibling `task_index`. The endpoint survives the fabro version
change (identical in 0.354 and 0.362), so this is the one channel that works on
both; it replaces an earlier design that `docker exec`'d into the sandbox over
the mounted Docker socket, which the scheduler no longer has (H6,
docs/fabro-upgrade/03).

Both sources are awkward to query per page load, and the operator wants them to
advance without hammering either fabro or a busy browser. So this is a
**background updater**: one thread refreshes an in-memory cache of per-run
`RunProgress` on a fixed beat, and the page renders from the cache synchronously
with no API work of its own.

Interval guidance: stages last tens of seconds to minutes each, so a 30-second
beat keeps the display visibly current while making only ~3 API calls per beat
(one for the stage list, two for the sandbox files), one per leased coder
instance and there are at most four. That is a rounding error on a localhost
fabro API.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from .fabro import FabroClient
from .lease import LeaseStore

log = logging.getLogger(__name__)

# The default beat. Configurable via SCHEDULER_RUN_PROBE_SECONDS so an operator
# can thin it out if it ever matters: at two boxes this is ~8 API calls a minute.
DEFAULT_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class RunProgress:
    """One run's live view, as of `fetched_at`.

    `stage_name`/`stage_visit` are the current stage's node_id and visit
    (`review_merge.validate` / `1`); the page links to
    `.../stages/{stage_name}@{stage_visit}`. `tasks_total` is the decomposed-task
    count or `None` when the sandbox is not ready or not reachable.
    """

    run_id: str
    status: str | None  # the current stage's status ("running"/"succeeded"/...)
    stage_name: str | None
    stage_visit: int | None
    tasks_completed: int | None  # completed / `tasks_total`, as `fabro-run-status.sh`
    tasks_total: int | None  # the decomposed-task count; can grow with extra review
    fetched_at: datetime
    error: str | None = None


class RunProbe:
    """The cache and its updater thread.

    Split for the same reason `DispatchLoop` is: `tick()` never touches a thread,
    so a test drives one refresh by hand with fake fabro/docker objects, and
    `start()`/`stop()` own the thread.
    """

    def __init__(
        self,
        fabro: FabroClient | None,
        leases: LeaseStore,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        # `fabro` may be None: the page still renders (columns show "—"), it just
        # never fills. There is no separate transport object: both the stage list
        # and the sandbox files come from the fabro API.
        self._fabro = fabro
        self._leases = leases
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Written only under `_lock`; the page reads via `_lock`.
        self._lock = threading.Lock()
        self._snapshots: dict[str, RunProgress] = {}

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def snapshot(self, run_id: str) -> RunProgress | None:
        with self._lock:
            return self._snapshots.get(run_id)

    # -- one beat ------------------------------------------------------------

    def tick(self) -> None:
        """Refresh every active lease's view, then prune ones that ended."""
        active = self._leases.active()
        active_ids = {lease.run_id for lease in active}

        with self._lock:
            for run_id in list(self._snapshots):
                if run_id not in active_ids:
                    del self._snapshots[run_id]

        for lease in active:
            try:
                progress = self._probe(lease.run_id)
            except Exception as exc:  # noqa: BLE001 - one run must not block the beat
                log.warning("run-probe: %s: %s", lease.run_id, exc)
                progress = RunProgress(
                    run_id=lease.run_id,
                    status=None,
                    stage_name=None,
                    stage_visit=None,
                    tasks_completed=None,
                    tasks_total=None,
                    fetched_at=datetime.now(UTC),
                    error=str(exc),
                )
            with self._lock:
                self._snapshots[lease.run_id] = progress

    def _probe(self, run_id: str) -> RunProgress:
        stage_name, stage_visit, status = self._current_stage(run_id)
        completed, total = self._task_progress(run_id)
        return RunProgress(
            run_id=run_id,
            status=status,
            stage_name=stage_name,
            stage_visit=stage_visit,
            tasks_completed=completed,
            tasks_total=total,
            fetched_at=datetime.now(UTC),
        )

    def _current_stage(self, run_id: str) -> tuple[str | None, int | None, str | None]:
        """The most recent not-finished stage: in progress if any, else the last.

        A run spends a moment between stages with none marked `running`, so the
        fallback (last by `started_at`) keeps the column from flickering to "—".
        """
        stages = self._fabro.get_stages(run_id) if self._fabro is not None else []
        if not stages:
            return None, None, None
        running = [s for s in stages if s.get("status") == "running"]
        pool = running or stages
        current = max(pool, key=lambda s: s.get("started_at") or "")
        visit = current.get("visit")
        return (
            current.get("node_id"),
            visit if isinstance(visit, int) else None,
            current.get("status"),
        )

    def _task_progress(self, run_id: str) -> tuple[int | None, int | None]:
        """`(completed, total)` from the sandbox, or `(None, None)` pre-decompose.

        `total` is the length of `tasks.json` — the authority on the task list, so
        it reflects any growth from an extra review. `completed` is `task_index −
        1` (fabro bumps the index after a task is selected), clamped to `[0, total]`
        so a consolidation that shrinks the list never shows a nonsense fraction.
        Both files are read through the fabro API by absolute sandbox path
        (verified against 0.354; a relative path resolves under the working dir
        and 404s). A missing file (404/409 → `None`), an unparsable `tasks.json`,
        or a `tasks.json` that is not a JSON array reads as "—".
        """
        if self._fabro is None:
            return None, None
        raw_tasks = self._fabro.read_sandbox_file(run_id, "/tmp/fabro/tasks.json")
        if raw_tasks is None:
            return None, None  # not found, or no active sandbox: decompose not done
        try:
            tasks = json.loads(raw_tasks)
        except ValueError:
            return None, None
        if not isinstance(tasks, list):
            return None, None
        total = len(tasks)
        raw_index = self._fabro.read_sandbox_file(run_id, "/tmp/fabro/task_index")
        try:
            index = int((raw_index or "").strip())
        except ValueError:
            index = 0
        completed = max(min(index - 1, total), 0)
        return completed, total

    # -- the thread ----------------------------------------------------------

    def start(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="fabro-scheduler-runprobe", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("run-probe beat failed; will retry next interval")
            self._stop.wait(self._interval)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
