"""The scorecard collector: summarizes released runs and reads their PRs (issue #22).

One thread, the same shape as `ReleasePoller`: `tick()` is the testable loop body,
and `start`/`stop` are the only things that touch a thread. It is a read-only
observer. It writes only the five scorecard tables, never a lease, a label or
`run_history`, so a fault here can cost the metrics page its freshness and nothing
else.

Each tick does a little of two jobs:

* **Summarize runs.** Every `run_history` row without a `run_summary` is work.
  The run's events are streamed from fabro page by page and reduced on the fly
  (`summary.summarize`), so a 25 MB log never sits in memory or on a disk. The
  first start after deploy backfills all of history this way, newest first,
  `runs_per_tick` at a time.
* **Read PRs.** A factory PR's facts are fetched once it has a release row and
  then re-fetched while it is open, at most every `pr_refresh`. A merged or closed
  PR is final. Three GETs per PR against the 5,000/hour budget the inventory
  poller shares, which spends almost none of it (its requests are conditional).
* **Read model usage.** A summarized run's `GET /runs/{id}/usage` is read once and
  its `by_model` rows stored for the Metrics tab's "Tokens by model" table. One
  small GET per run, so a deploy backfills history in a few ticks.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .fabro import FabroClient, FabroError
from .github import GitHubError
from .prfacts import PrFacts, fetch_pr_facts
from .store import Store
from .summary import summarize
from .usage import model_rows

log = logging.getLogger(__name__)

DEFAULT_COLLECT_SECONDS = 30
# Enough to backfill a few hundred runs within a couple of hours of a deploy, and
# small enough that one tick (each run is up to ~25 MB streamed) stays short.
DEFAULT_RUNS_PER_TICK = 2
DEFAULT_PRS_PER_TICK = 10
# One small JSON answer each, not an event stream.
DEFAULT_MODELS_PER_TICK = 20
# A run is summarized once it has been released this long: fabro writes the
# sandbox-stop events after the terminal state the release poll acted on.
SETTLE = timedelta(minutes=2)
RETRY_AFTER = timedelta(hours=1)
PR_REFRESH = timedelta(minutes=30)


@dataclass(frozen=True)
class CollectReport:
    summarized: int = 0
    missing: int = 0
    failed: int = 0
    prs: int = 0
    pr_failures: int = 0
    models: int = 0
    model_failures: int = 0


class ScorecardCollector:
    def __init__(
        self,
        store: Store,
        fabro_client: FabroClient,
        github_token: str,
        automation_author: str,
        *,
        interval_seconds: float = DEFAULT_COLLECT_SECONDS,
        runs_per_tick: int = DEFAULT_RUNS_PER_TICK,
        prs_per_tick: int = DEFAULT_PRS_PER_TICK,
        models_per_tick: int = DEFAULT_MODELS_PER_TICK,
        pr_reader: Callable[[str, int], PrFacts] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._fabro = fabro_client
        self._interval = interval_seconds
        self._runs_per_tick = runs_per_tick
        self._prs_per_tick = prs_per_tick
        self._models_per_tick = models_per_tick
        self._now = now or (lambda: datetime.now(UTC))
        self._read_pr = pr_reader or (
            lambda repo, number: fetch_pr_facts(repo, number, github_token, automation_author)
        )
        self._github = bool(github_token.strip()) or pr_reader is not None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _task_sources(self, run_id: str) -> Counter[str]:
        """A `ralph` run's Task list counted by `source`. Never raises.

        The summary is worth having even when this read fails, so a 404 (the sandbox
        is gone), a malformed body or a run whose graph wrote no `tasks.json` all
        return an empty Counter.
        """
        try:
            raw = self._fabro.read_sandbox_file(run_id, "/tmp/fabro/tasks.json")
        except FabroError:
            return Counter()
        if not raw:
            return Counter()
        try:
            tasks = json.loads(raw)
        except ValueError:
            return Counter()
        if not isinstance(tasks, list):
            return Counter()
        return Counter(
            str(task.get("source") or "unknown")
            for task in tasks
            if isinstance(task, Mapping)
        )

    def tick(self) -> CollectReport:
        """One pass of both jobs. Errors per run or PR are recorded, not raised."""
        now = self._now()
        summarized = missing = failed = 0
        for run_id in self._store.runs_to_summarize(
            finished_before=now - SETTLE,
            retry_before=now - RETRY_AFTER,
            limit=self._runs_per_tick,
        ):
            try:
                workflow = self._store.history_workflow(run_id)
                summary = summarize(
                    run_id,
                    self._fabro.summary_stages(run_id),
                    self._fabro.iter_events(run_id),
                    workflow=workflow,
                )
                if workflow == "ralph":
                    # C12: the Tasks by `source` line needs the Task list, which only
                    # the run has. One read at summary time, as the probe does; a
                    # missing file or an older graph leaves the counts empty, which
                    # the scorecard renders as "not measured" rather than zero.
                    summary.task_sources = self._task_sources(run_id)
            except FabroError as exc:
                status = "missing" if exc.status_code == 404 else "error"
                self._store.save_summary_failure(run_id, status, str(exc), at=now)
                if status == "missing":
                    missing += 1
                else:
                    failed += 1
                    log.warning("scorecard: %s could not be summarized: %s", run_id, exc)
                continue
            self._store.save_run_summary(summary, at=now)
            summarized += 1

        models = model_failures = 0
        for run_id in self._store.runs_to_read_models(
            retry_before=now - RETRY_AFTER, limit=self._models_per_tick
        ):
            try:
                body = self._fabro.run_usage(run_id)
            except FabroError as exc:
                status = "missing" if exc.status_code == 404 else "error"
                self._store.save_model_usage_failure(run_id, status, at=now)
                if status == "error":
                    model_failures += 1
                    log.warning("scorecard: %s model usage failed: %s", run_id, exc)
                continue
            by_model = body.get("by_model") if isinstance(body, Mapping) else None
            self._store.save_model_usage(run_id, model_rows(by_model), at=now)
            models += 1

        prs = pr_failures = 0
        if self._github:
            for repo, number in self._store.prs_to_refresh(
                stale_before=now - PR_REFRESH, limit=self._prs_per_tick
            ):
                try:
                    facts = self._read_pr(repo, number)
                except GitHubError as exc:
                    pr_failures += 1
                    log.warning("scorecard: %s#%s PR facts failed: %s", repo, number, exc)
                    if exc.status_code == 403:
                        break  # most likely the rate limit; the next tick tries again
                    continue
                self._store.save_pr_facts(repo, number, facts, at=now)
                prs += 1

        report = CollectReport(
            summarized, missing, failed, prs, pr_failures, models, model_failures
        )
        if summarized or missing or failed or prs or pr_failures or models or model_failures:
            log.info(
                "scorecard: %d run(s) summarized, %d missing, %d failed; "
                "%d PR(s) read, %d failed; %d model usage(s) read, %d failed",
                summarized,
                missing,
                failed,
                prs,
                pr_failures,
                models,
                model_failures,
            )
        return report

    def start(self) -> None:
        """Start collecting. Idempotent."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="fabro-scheduler-scorecard", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None

    def _run(self) -> None:
        """Tick, then wait `interval`, until stopped. A raise is logged, not fatal."""
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - one bad tick must not stop the thread
                log.exception("scorecard: tick failed")
            if self._stop.wait(self._interval):
                return
