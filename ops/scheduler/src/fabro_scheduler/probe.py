"""The run probe: cached per-lease task count and current stage for the page.

The queue page's "Coder instances" table shows two live facts about a running
run that fabro's run projection does not carry directly on the page render:

* the number of tasks the issue was decomposed into (`/tmp/fabro/tasks.json` in
  the sandbox — same datum `ops/fabro-run-status.sh` reads), and
* the workflow stage currently in progress (`review_merge.validate` etc.), linked
  to `.../runs/{id}/stages/{node}@{visit}`.

Both come from sources that are awkward to query per page load — one a `docker
exec` into the sandbox (via the mounted socket), the other a fabro API call —
and the operator wants them to advance without hammering either fabro or a busy
browser. So this is a **background updater**: one thread refreshes an in-memory
cache of per-run `RunProgress` on a fixed beat, and the page renders from the
cache synchronously with no API or socket work of its own.

Interval guidance: stages last tens of seconds to minutes each, so a 30-second
beat keeps the display visibly current while making only ~2 API calls and ~2
sandbox reads per beat (one per leased coder instance, and there are at most
four). That is a rounding error on a localhost fabro API and on a LAN socket.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from .docker import DockerClient
from .fabro import FabroClient
from .lease import LeaseStore

log = logging.getLogger(__name__)

# The default beat. Configurable via SCHEDULER_RUN_PROBE_SECONDS so an operator
# can thin it out if it ever matters: at two boxes this is ~8 API calls a minute
# and one trivial `jq length` per box per beat.
DEFAULT_INTERVAL_SECONDS = 30.0

TASKS_CMD = [
    "sh",
    "-c",
    # Present after decompose; a missing file (run still in `prep`/`decompose`)
    # prints nothing, which the probe reads as "not yet decomposed" → "—".
    # `jq 'length'` reports the size of the array even when it is `[]` (0).
    "if [ -f /tmp/fabro/tasks.json ]; then jq -r 'length' /tmp/fabro/tasks.json; fi",
]


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
    tasks_total: int | None
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
        docker: DockerClient,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        # `fabro` may be None: the page still renders (columns show "—"), it just
        # never fills. `docker` is required because there is no meaning to a probe
        # that cannot read the sandbox.
        self._fabro = fabro
        self._leases = leases
        self._docker = docker
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Written only under `_lock`; the page reads via `_lock`.
        self._lock = threading.Lock()
        self._snapshots: dict[str, RunProgress] = {}
        # Sandbox container id per run: stable for a run's lifetime, so fetched
        # once and reused until the lease disappears.
        self._sandbox_ids: dict[str, str] = {}

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
                    self._sandbox_ids.pop(run_id, None)

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
                    tasks_total=None,
                    fetched_at=datetime.now(UTC),
                    error=str(exc),
                )
            with self._lock:
                self._snapshots[lease.run_id] = progress

    def _probe(self, run_id: str) -> RunProgress:
        stage_name, stage_visit, status = self._current_stage(run_id)
        tasks = self._task_count(run_id)
        return RunProgress(
            run_id=run_id,
            status=status,
            stage_name=stage_name,
            stage_visit=stage_visit,
            tasks_total=tasks,
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

    def _task_count(self, run_id: str) -> int | None:
        container_id = self._sandbox_ids.get(run_id)
        if container_id is None:
            if self._fabro is None:
                return None
            run = self._fabro.get_run(run_id)
            runtime = (run.get("sandbox") or {}).get("instance", {}).get("runtime", {})
            container_id = runtime.get("id")
            if not container_id:
                return None  # sandbox not ready yet; try again next beat
            self._sandbox_ids[run_id] = container_id
        raw = self._docker.exec(container_id, TASKS_CMD).strip()
        if not raw:
            return None  # tasks.json not present → decompose not done (or empty)
        try:
            return int(raw)
        except ValueError:
            return None

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
